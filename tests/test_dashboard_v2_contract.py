"""Contract tests for the current frontend-debt Dashboard additions."""

from urllib.parse import quote

from fastapi.testclient import TestClient

from tests.dashboard_fixtures import DashboardFixtureFactory
from tests.test_auth_flow import _login_as_admin
from tests.test_dashboard_auth import _login_as_fixture_employee


def test_dashboard_search_is_server_side_and_ignores_spaces(
    client: TestClient,
    dashboard_factory: DashboardFixtureFactory,
):
    record = dashboard_factory.create(
        analysis_condition={"detail": "샘플 데이터 승인 대기"},
    )
    headers = _login_as_fixture_employee(client, record)

    response = client.get(
        "/api/v1/dashboard/tasks",
        params={"search": "대시보드계약", "page": 1, "page_size": 30},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scope"] == "mine"
    assert body["total_count"] == 1
    assert body["items"][0]["request_no"] == record.request_no


def test_admin_overview_and_all_scope_are_separate_from_personal_dashboard(
    client: TestClient,
    dashboard_factory: DashboardFixtureFactory,
):
    record = dashboard_factory.create()
    headers = _login_as_admin(client)

    personal = client.get("/api/v1/dashboard", headers=headers)
    overview = client.get("/api/v1/dashboard/overview", headers=headers)
    all_tasks = client.get(
        "/api/v1/dashboard/tasks",
        params={"scope": "all", "search": record.request_no, "page_size": 30},
        headers=headers,
    )

    assert personal.status_code == 200
    assert personal.json()["scope"] == "mine"
    assert overview.status_code == 200
    assert overview.json()["scope"] == "all"
    assert all_tasks.status_code == 200
    assert all_tasks.json()["total_count"] == 1
    assert all_tasks.json()["items"][0]["request_no"] == record.request_no


def test_priority_list_uses_the_same_actionable_scope_as_priority_cards(
    client: TestClient,
    dashboard_factory: DashboardFixtureFactory,
):
    actionable = dashboard_factory.create(
        pipeline_status="WAITING_FINAL_REVIEW",
        current_stage="DATA_PROCESSING",
        stage_code="DATA_PROCESSING",
    )
    historical = dashboard_factory.create(
        pipeline_status="RUNNING",
        current_stage="DATA_PROCESSING",
        stage_code="DATA_PROCESSING",
        review_type="FINAL",
        review_decision="APPROVED",
    )
    headers = _login_as_admin(client)

    response = client.get(
        "/api/v1/dashboard/tasks",
        params={"scope": "all", "priority": "FINAL", "page_size": 30},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert [item["request_no"] for item in response.json()["items"]] == [actionable.request_no]
    assert historical.request_no not in [item["request_no"] for item in response.json()["items"]]


def test_integrated_detail_returns_stages_history_and_artifacts_shape(
    client: TestClient,
    dashboard_factory: DashboardFixtureFactory,
):
    record = dashboard_factory.create(
        pipeline_status="WAITING_SAMPLE_REVIEW",
        current_stage="DATA_SELECTION",
        stage_code="DATA_SELECTION",
        review_type="SAMPLE",
    )
    headers = _login_as_admin(client)
    run_id = record.pipeline_run.id

    response = client.get(
        f"/api/v1/tasks/{quote(record.request_no, safe='')}/runs/{run_id}/detail",
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["request_no"] == record.request_no
    assert body["run_id"] == run_id
    assert {"stages", "history", "available_actions"} <= body.keys()
    assert body["available_actions"] == ["APPROVE", "REQUEST_CHANGES"]
