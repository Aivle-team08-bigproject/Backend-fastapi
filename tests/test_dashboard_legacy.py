"""Regression specifications for employee-scoped my-tasks and task views."""

from urllib.parse import quote

from fastapi.testclient import TestClient

from tests.dashboard_fixtures import DashboardFixtureFactory


LEGACY_MY_TASK_KEYS = {
    "employee_code",
    "user_name",
    "department",
    "active_count",
    "urgent_count",
    "completed_count",
    "completion_rate",
    "tasks",
}
LEGACY_TASK_ROW_KEYS = {
    "request_no",
    "client",
    "data_type",
    "detail",
    "assignee",
    "created_at",
    "updated_at",
    "status",
}


def _login_as_fixture_employee(client: TestClient, record) -> dict:
    response = client.post(
        "/api/auth/login",
        json={
            "employee_code": record.employee.employee_code,
            "password": record.password,
            "remember_me": False,
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_my_tasks_remains_employee_scoped_and_preserves_legacy_fields(
    client: TestClient,
    dashboard_factory: DashboardFixtureFactory,
):
    mine = dashboard_factory.create(
        analysis_condition={"dashboard_status": "요구사항 분석"},
        pipeline_status="RUNNING",
        current_stage="REQUIREMENT_ANALYSIS",
        stage_code="REQUIREMENT_ANALYSIS",
    )
    another_employee = dashboard_factory.create(
        analysis_condition={"dashboard_status": "데이터 가공 진행"},
        pipeline_status="RUNNING",
        current_stage="DATA_PROCESSING",
        stage_code="DATA_PROCESSING",
    )

    response = client.get(
        "/api/v1/dashboard/my-tasks",
        headers=_login_as_fixture_employee(client, mine),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == LEGACY_MY_TASK_KEYS
    assert body["employee_code"] == mine.employee.employee_code
    assert body["user_name"] == mine.employee.name
    assert body["department"] == mine.employee.department
    assert {task["request_no"] for task in body["tasks"]} == {mine.request_no}
    assert another_employee.request_no not in {task["request_no"] for task in body["tasks"]}
    for task in body["tasks"]:
        assert LEGACY_TASK_ROW_KEYS <= set(task)


def test_task_view_preserves_snapshot_payload_and_encoded_request_transport(
    client: TestClient,
    dashboard_factory: DashboardFixtureFactory,
):
    record = dashboard_factory.create(
        request_no="REQ/legacy encoded value",
        view_code="VIEW/legacy encoded value",
        snapshot_payload={"rows": [{"source": "TaskViewSnapshot", "value": 42}]},
    )
    response = client.get(
        f"/api/v1/tasks/{quote(record.request_no, safe='')}/views/{quote(record.view_code, safe='')}",
        headers=_login_as_fixture_employee(client, record),
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "request_no": record.request_no,
        "request_title": record.data_request.title,
        "view_code": record.view_code,
        "payload": record.snapshot.payload,
    }
