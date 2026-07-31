"""Supervisor 전체 흐름 통합테스트 — 스텁 에이전트로 실제 DB·API를 거친다.

검증하는 계약:
    요청 생성 -> dispatch -> 단계 실행 -> HITL 게이트 정지 -> 승인 -> 다음 단계
    -> ... -> 최종 승인 -> COMPLETED

Celery는 eager 모드로 돌려서 브로커 없이 인라인 실행한다. 외부 API 호출은 없다
(에이전트를 스텁으로 갈아끼운다). Redis가 없어도 publish_to_screen이 예외를 삼키므로
그대로 통과한다 — 상태 정본은 DB라는 설계가 실제로 성립하는지도 같이 확인된다.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.domains.pipeline import supervisor
from app.worker.celery_app import celery_app
from tests.test_auth_flow import _login_as_admin


def _sync_engine():
    """검증용 동기 엔진 — API가 쓴 결과를 앱 세션 밖에서 읽는다."""
    return create_engine(settings.portfolio_app_database_url, future=True)


class StubAgents:
    """각 단계의 검증 스키마를 통과하는 최소 산출물."""

    def __init__(self):
        self.calls: list[str] = []

    async def run(self, agent_name: str, model_name: str, payload: dict) -> dict:
        self.calls.append(agent_name)

        if agent_name == "requirement-analysis-agent":
            return {
                "usage_purpose": "consumption_trend",
                "requested_data_sentence": "서울 지역 결제 데이터",
                "categories": {"region": ["capital_area"]},
                "delivery_channel": "FILE_DOWNLOAD",
                "output_formats": ["csv"],
            }

        if agent_name == "data-selection-agent":
            columns = [
                {
                    "name": "merchant_region",
                    "data_type": "string",
                    "is_derived": False,
                    "source_columns": ["merchant_region"],
                    "description": "가맹점 지역",
                },
                {
                    "name": "결제건수",
                    "data_type": "integer",
                    "is_derived": True,
                    "source_columns": ["transaction_id"],
                    "description": "지역별 거래 ID 개수",
                },
            ]
            return {
                "selected_tables": [
                    {"table": "transaction_pseudonymized", "reason": "결제 분석"},
                    {"table": "merchant", "reason": "지역 정보"},
                ],
                "source_columns": [
                    {
                        "dataset": "transaction_pseudonymized",
                        "column": "transaction_id",
                        "data_type": "character varying",
                        "comment": "거래 식별자",
                        "reason": "결제 건수 계산",
                    },
                    {
                        "dataset": "merchant",
                        "column": "merchant_region",
                        "data_type": "character varying",
                        "comment": "가맹점 지역",
                        "reason": "지역 구분",
                    },
                ],
                "derived_columns": [
                    {
                        "name": "결제건수",
                        "data_type": "integer",
                        "source_columns": ["transaction_id"],
                        "derivation": "merchant_region별 transaction_id 개수",
                        "description": "지역별 결제 건수",
                    }
                ],
                "selection_query": {
                    "columns": ["transaction_id", "merchant_region"],
                    "filters": {},
                },
                "sample_columns": columns,
                "sample_rows": [
                    {"merchant_region": "서울", "결제건수": i}
                    for i in range(1, 6)
                ],
                "sample_metadata": {"is_synthetic": True, "sample_count": 5},
            }

        if agent_name == "data-processing-agent":
            return {
                "processed_columns": ["지역", "결제건수"],
                "api_result": {"items": [], "meta": {}},
                "csv_columns": ["지역", "결제건수"],
                "visualization": {"chart_type": "bar", "x": "지역", "y": "결제건수"},
                "report": {"title": "결과", "summary": "완료"},
                "processing_explanation": {"summary": "stub"},
                "quality_report": {"input_row_count": 5, "output_row_count": 5},
            }

        raise AssertionError(f"unexpected agent: {agent_name}")


@pytest.fixture()
def stub_agents(monkeypatch):
    """Supervisor가 실제 에이전트 대신 스텁을 쓰게 하고 Celery를 eager로 돌린다."""
    stub = StubAgents()
    monkeypatch.setattr(supervisor, "AgentRuntimeClient", lambda: stub)

    previous_eager = celery_app.conf.task_always_eager
    previous_propagate = celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    try:
        yield stub
    finally:
        celery_app.conf.task_always_eager = previous_eager
        celery_app.conf.task_eager_propagates = previous_propagate


def _create_run(client: TestClient) -> int:
    marker = uuid.uuid4().hex[:8]
    response = client.post(
        "/api/v1/data-requests",
        json={
            "raw_requirement": f"서울 지역 결제 데이터를 CSV로 주세요. {marker}",
            "title": f"수퍼바이저 흐름 테스트 {marker}",
            "requester_name": f"흐름 테스트 요청자 {marker}",
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def _stage_rows(run_id: int) -> list[tuple]:
    with _sync_engine().connect() as connection:
        return list(
            connection.execute(
                text(
                    "select stage_code, status, attempt_no from service.stage_runs "
                    "where pipeline_run_id = :run_id "
                    "order by attempt_no, id"
                ),
                {"run_id": run_id},
            )
        )


def _run_row(run_id: int) -> tuple:
    with _sync_engine().connect() as connection:
        return connection.execute(
            text(
                "select status, current_stage, progress_percent, rollback_to_stage "
                "from service.pipeline_runs where id = :run_id"
            ),
            {"run_id": run_id},
        ).one()


def test_full_approval_path_walks_every_stage_and_completes(client, stub_agents):
    run_id = _create_run(client)
    headers = _login_as_admin(client)

    # 요청 생성만으로 첫 단계가 돌고 첫 게이트에서 멈춘다.
    status, current_stage, progress, rollback = _run_row(run_id)
    assert status == "WAITING_REQUIREMENT_REVIEW"
    assert current_stage == "REQUIREMENT_ANALYSIS"
    assert progress == 33
    assert rollback is None
    assert stub_agents.calls == ["requirement-analysis-agent"]

    # 1차 승인 -> 데이터 선별이 돌고 두 번째 게이트에서 멈춘다.
    response = client.post(f"/api/v1/runs/{run_id}/review", json={"approved": True}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"] == "APPROVED"
    assert body["next_stage"] == "DATA_SELECTION"
    assert _run_row(run_id)[0] == "WAITING_SAMPLE_REVIEW"
    assert stub_agents.calls[-1] == "data-selection-agent"

    # 2차 승인 -> 데이터 가공이 돌고 최종 게이트에서 멈춘다.
    response = client.post(f"/api/v1/runs/{run_id}/review", json={"approved": True}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["next_stage"] == "DATA_PROCESSING"
    assert _run_row(run_id)[0] == "WAITING_FINAL_REVIEW"
    assert stub_agents.calls[-1] == "data-processing-agent"

    # 최종 승인 -> COMPLETED, 더 진행할 단계 없음.
    response = client.post(f"/api/v1/runs/{run_id}/review", json={"approved": True}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_status"] == "COMPLETED"
    assert body["next_stage"] is None

    status, _, progress, _ = _run_row(run_id)
    assert status == "COMPLETED"
    assert progress == 100
    assert [code for code, _, _ in _stage_rows(run_id)] == [
        "REQUIREMENT_ANALYSIS",
        "DATA_SELECTION",
        "DATA_PROCESSING",
    ]
    assert {state for _, state, _ in _stage_rows(run_id)} == {"COMPLETED"}
    assert stub_agents.calls == [
        "requirement-analysis-agent",
        "data-selection-agent",
        "data-processing-agent",
    ]


def test_review_is_rejected_when_run_is_not_at_a_gate(client, stub_agents):
    run_id = _create_run(client)
    headers = _login_as_admin(client)

    # 첫 게이트를 소비하고 나면 다음 단계가 돌아 다른 게이트로 옮겨간다.
    client.post(f"/api/v1/runs/{run_id}/review", json={"approved": True}, headers=headers)
    # 완주시켜 COMPLETED로 만든다.
    client.post(f"/api/v1/runs/{run_id}/review", json={"approved": True}, headers=headers)
    client.post(f"/api/v1/runs/{run_id}/review", json={"approved": True}, headers=headers)
    assert _run_row(run_id)[0] == "COMPLETED"

    # COMPLETED 상태에서의 추가 검토는 409로 거부된다.
    response = client.post(f"/api/v1/runs/{run_id}/review", json={"approved": True}, headers=headers)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "PIPELINE_RUN_NOT_UNDER_REVIEW"


def test_rejection_rolls_back_and_creates_a_new_attempt(client, stub_agents):
    run_id = _create_run(client)
    headers = _login_as_admin(client)

    # 데이터 선별 게이트까지 전진.
    client.post(f"/api/v1/runs/{run_id}/review", json={"approved": True}, headers=headers)
    assert _run_row(run_id)[0] == "WAITING_SAMPLE_REVIEW"

    # INSUFFICIENT_DATA 반려 -> DATA_SELECTION으로 되돌아가 재실행된다.
    response = client.post(
        f"/api/v1/runs/{run_id}/review",
        json={
            "approved": False,
            "feedback": "선별된 데이터가 부족합니다.",
            "failure_code": "INSUFFICIENT_DATA",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"] == "CHANGES_REQUESTED"
    assert body["rollback_to_stage"] == "DATA_SELECTION"

    rows = _stage_rows(run_id)
    # 1차 시도의 DATA_SELECTION/DATA_PROCESSING은 ROLLED_BACK, 2차 시도 행이 새로 생긴다.
    first_attempt = {code: state for code, state, attempt in rows if attempt == 1}
    second_attempt = {code: state for code, state, attempt in rows if attempt == 2}
    assert first_attempt["REQUIREMENT_ANALYSIS"] == "COMPLETED"
    assert first_attempt["DATA_SELECTION"] == "ROLLED_BACK"
    assert set(second_attempt) == {"DATA_SELECTION", "DATA_PROCESSING"}

    # 되돌아간 단계가 다시 실행되어 같은 게이트에 멈춘다.
    assert _run_row(run_id)[0] == "WAITING_SAMPLE_REVIEW"
    assert second_attempt["DATA_SELECTION"] == "COMPLETED"
    assert stub_agents.calls == [
        "requirement-analysis-agent",
        "data-selection-agent",
        "data-selection-agent",
    ]
