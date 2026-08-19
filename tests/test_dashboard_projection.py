"""Wave 1 specifications for the persisted dashboard workflow projection."""

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.db.session import AsyncSessionLocal
from app.domains.pipeline.model import PipelineRun, StageRun
from tests.dashboard_fixtures import DashboardFixtureFactory
from tests.test_auth_flow import _login_as_admin


PRIORITY_CODES = {"REQUIREMENT", "SAMPLE", "FINAL"}
STAGE_GROUP_CODES = {
    "REQUIREMENT_ANALYSIS",
    "SAMPLE_DATA",
    "FINAL_OUTPUT",
    "COMPLETED",
    "UNKNOWN",
}


def _task_item(body: dict, request_no: str) -> dict:
    """Return one projected row while allowing unrelated shared-db fixtures."""

    return next(item for item in body["items"] if item["request_no"] == request_no)


def _dashboard_item(body: dict, collection: str, request_no: str) -> dict:
    return next(item for item in body[collection] if item["request_no"] == request_no)


async def _add_pipeline_attempt_with_stages(record) -> None:
    """Create retry and equal-timestamp stage rows for latest-row selection tests."""

    assert record.pipeline_run is not None
    base_time = record.pipeline_run.created_at.replace(tzinfo=timezone.utc)
    latest_run = PipelineRun(
        data_request_id=record.data_request.id,
        attempt_no=2,
        status="RUNNING",
        current_stage="REQUIREMENT_ANALYSIS",
        progress_percent=70,
        started_at=base_time + timedelta(minutes=3),
        created_at=base_time + timedelta(minutes=3),
        updated_at=base_time + timedelta(minutes=4),
    )
    async with AsyncSessionLocal() as db:
        db.add(latest_run)
        await db.flush()
        db.add_all(
            [
                StageRun(
                    pipeline_run_id=latest_run.id,
                    stage_code="DATA_SELECTION",
                    attempt_no=1,
                    status="COMPLETED",
                    executor="TEST",
                    input_payload={},
                    output_payload={},
                    validation_result={},
                    created_at=base_time + timedelta(minutes=4),
                ),
                StageRun(
                    pipeline_run_id=latest_run.id,
                    stage_code="DATA_PROCESSING",
                    attempt_no=1,
                    status="RUNNING",
                    executor="TEST",
                    input_payload={},
                    output_payload={},
                    validation_result={},
                    created_at=base_time + timedelta(minutes=5),
                ),
                StageRun(
                    pipeline_run_id=latest_run.id,
                    stage_code="REQUIREMENT_ANALYSIS",
                    attempt_no=1,
                    status="COMPLETED",
                    executor="TEST",
                    input_payload={},
                    output_payload={},
                    validation_result={},
                    created_at=base_time + timedelta(minutes=5),
                ),
            ]
        )
        await db.commit()


def test_projection_reads_real_workflow_and_keeps_incomplete_rows(
    client: TestClient,
    dashboard_factory: DashboardFixtureFactory,
):
    without_demo = dashboard_factory.create(
        analysis_condition={"dashboard_status": "가짜 데모 상태"},
        pipeline_status="RUNNING",
        current_stage="DATA_PROCESSING",
        stage_code="DATA_PROCESSING",
    )
    with_demo = dashboard_factory.create(
        analysis_condition={
            "demo_dashboard": True,
            "dashboard_status": "완료",
            "assignee": "가짜 담당자",
        },
        pipeline_status="RUNNING",
        current_stage="DATA_PROCESSING",
        stage_code="DATA_PROCESSING",
    )
    incomplete = dashboard_factory.create(
        include_pipeline=False,
        include_stage=False,
        include_owner=False,
        analysis_condition={"demo_dashboard": False},
    )

    response = client.get(
        "/api/v1/dashboard/tasks",
        params={"page": 1, "page_size": 100},
        headers=_login_as_admin(client),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    for record in (without_demo, with_demo, incomplete):
        item = _task_item(body, record.request_no)
        assert item["request_no"] == record.data_request.request_no
        assert item["client"] == record.client.company_name

    demo_item = _task_item(body, with_demo.request_no)
    assert demo_item["status_code"] == "RUNNING"
    assert demo_item["stage_code"] == "DATA_PROCESSING"
    assert demo_item["assignee_name"] == with_demo.employee.name
    assert demo_item["assignee_name"] != "가짜 담당자"

    incomplete_item = _task_item(body, incomplete.request_no)
    assert incomplete_item["stage_group_code"] == "UNKNOWN"
    assert incomplete_item["stage_label"] == "상태 확인 필요"
    assert incomplete_item["assignee_name"] == "미배정"
    assert incomplete_item["assignee_code"] is None
    assert incomplete_item["priority_code"] is None


def test_projection_uses_newest_attempt_and_stage_tie_breaker_only(
    client: TestClient,
    dashboard_factory: DashboardFixtureFactory,
):
    record = dashboard_factory.create(
        include_stage=False,
        pipeline_status="RUNNING",
        current_stage="STALE_PIPELINE_STAGE",
    )
    asyncio.run(_add_pipeline_attempt_with_stages(record))

    response = client.get(
        "/api/v1/dashboard/tasks",
        params={"page": 1, "page_size": 100},
        headers=_login_as_admin(client),
    )

    assert response.status_code == 200, response.text
    item = _task_item(response.json(), record.request_no)
    # The newest StageRun wins: the last equal-timestamp insert has the largest id.
    assert item["stage_code"] == "REQUIREMENT_ANALYSIS"
    assert item["stage_group_code"] == "REQUIREMENT_ANALYSIS"
    # A stale PipelineRun.current_stage is diagnostic only, not the display source.
    assert item["stage_code"] != record.pipeline_run.current_stage


def test_projection_derives_priority_from_waiting_review_contract(
    client: TestClient,
    dashboard_factory: DashboardFixtureFactory,
):
    pending = dashboard_factory.create(
        pipeline_status="WAITING_REQUIREMENT_REVIEW",
        current_stage="REQUIREMENT_ANALYSIS",
        stage_code="REQUIREMENT_ANALYSIS",
    )
    approved = dashboard_factory.create(
        pipeline_status="WAITING_SAMPLE_REVIEW",
        current_stage="DATA_SELECTION",
        stage_code="DATA_SELECTION",
        review_type="SAMPLE",
        review_decision="APPROVED",
    )
    changes_requested = dashboard_factory.create(
        pipeline_status="WAITING_FINAL_REVIEW",
        current_stage="DATA_PROCESSING",
        stage_code="DATA_PROCESSING",
        review_type="FINAL",
        review_decision="CHANGES_REQUESTED",
    )
    unknown_review = dashboard_factory.create(
        pipeline_status="WAITING_FINAL_REVIEW",
        current_stage="DATA_PROCESSING",
        stage_code="DATA_PROCESSING",
        review_type="UNSUPPORTED_REVIEW",
        review_decision="CHANGES_REQUESTED",
    )

    dashboard = client.get(
        "/api/v1/dashboard",
        headers=_login_as_admin(client),
    )

    assert dashboard.status_code == 200, dashboard.text
    body = dashboard.json()
    actions = body["priority_actions"]
    action_by_request = {item["request_no"]: item for item in actions}
    assert action_by_request[pending.request_no]["priority_code"] == "REQUIREMENT"
    assert action_by_request[pending.request_no]["decision_status"] == "pending"
    assert action_by_request[pending.request_no]["requires_action"] is True
    assert action_by_request[changes_requested.request_no]["priority_code"] == "FINAL"
    assert action_by_request[changes_requested.request_no]["decision_status"] == "changes_requested"
    assert action_by_request[changes_requested.request_no]["requires_action"] is True
    assert approved.request_no not in action_by_request
    assert unknown_review.request_no not in action_by_request

    card_by_priority = {card["priority_code"]: card for card in body["priority_cards"]}
    assert set(card_by_priority) == PRIORITY_CODES
    assert card_by_priority["REQUIREMENT"]["count"] >= 1
    assert card_by_priority["FINAL"]["count"] >= 1
    assert all(card["priority_code"] in PRIORITY_CODES for card in body["priority_cards"])


def test_completed_work_is_visible_but_excluded_from_priority_aggregates(
    client: TestClient,
    dashboard_factory: DashboardFixtureFactory,
):
    completed = dashboard_factory.create(
        pipeline_status="COMPLETED",
        current_stage="COMPLETED",
        # Production runs can finish with the last real stage still selected;
        # the terminal PipelineRun status must classify the task as completed.
        stage_code="DATA_PROCESSING",
        stage_status="COMPLETED",
        review_type="FINAL",
        review_decision="APPROVED",
    )

    dashboard = client.get("/api/v1/dashboard", headers=_login_as_admin(client))
    tasks = client.get(
        "/api/v1/dashboard/tasks",
        params={"page": 1, "page_size": 100},
        headers=_login_as_admin(client),
    )

    assert dashboard.status_code == 200, dashboard.text
    assert tasks.status_code == 200, tasks.text
    assert _task_item(tasks.json(), completed.request_no)["stage_group_code"] == "COMPLETED"
    assert completed.request_no not in {
        item["request_no"]
        for collection in ("priority_actions", "approval_tasks")
        for item in dashboard.json()[collection]
    }
    assert len(dashboard.json()["priority_actions"]) <= 5
    assert len(dashboard.json()["approval_tasks"]) <= 5
