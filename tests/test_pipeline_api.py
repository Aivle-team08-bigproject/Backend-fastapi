from uuid import uuid4
from unittest.mock import Mock

from fastapi.testclient import TestClient

from tests.test_auth_flow import _login_as_admin


def test_create_request_then_read_pipeline_run(client: TestClient, monkeypatch):
    schedule = Mock()
    monkeypatch.setattr("app.domains.pipeline.service._schedule_pipeline_execution", schedule)
    marker = uuid4().hex[:8]
    headers = _login_as_admin(client)
    created = client.post(
        "/api/v1/data-requests",
        headers=headers,
        json={
            "raw_requirement": f"서울 지역 시간대별 결제 밀도를 분석해주세요. {marker}",
            "title": f"프론트 연동 테스트 {marker}",
            "requester_name": f"연동 테스트 요청자 {marker}",
        },
    )

    assert created.status_code == 202
    created_body = created.json()
    assert created_body["request_status"] == "QUEUED"
    assert created_body["run_status"] == "QUEUED"
    assert created_body["current_stage"] == "REQUIREMENT_ANALYSIS"
    assert created_body["execution_id"]
    schedule.assert_called_once_with(created_body["run_id"], created_body["execution_id"])

    response = client.get(f"/api/v1/runs/{created_body['run_id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["request_no"] == created_body["request_no"]
    assert body["request_title"] == f"프론트 연동 테스트 {marker}"
    assert [stage["status"] for stage in body["stages"]] == ["PENDING"] * 3
    assert [stage["executor"] for stage in body["stages"]] == ["CELERY"] * 3
    assert body["execution_id"] == created_body["execution_id"]
    assert body["events"] == []

    response = client.get("/api/v1/runs/2147483647")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "PIPELINE_RUN_NOT_FOUND"

    response = client.get(
        f"/api/v1/runs/{created_body['run_id']}/sample-preview",
        headers=headers,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "PIPELINE_SAMPLE_NOT_READY"

    response = client.post(f"/api/v1/runs/{created_body['run_id']}/input-csv")

    assert response.status_code == 404
