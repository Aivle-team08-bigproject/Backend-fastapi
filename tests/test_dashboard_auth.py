"""Direct authentication and all-active-employee read-access specifications."""

from pathlib import Path
from urllib.parse import quote
import pytest

from fastapi.testclient import TestClient

from tests.dashboard_fixtures import DashboardFixtureFactory
from tests.test_auth_flow import _login_as_admin


READ_ROUTES = (
    "/api/v1/dashboard",
    "/api/v1/dashboard/tasks",
    "/api/v1/dashboard/overview",
    "/api/v1/dashboard/task-lookup",
)


def _login_as_fixture_employee(client: TestClient, record) -> dict:
    response = client.post(
        "/api/auth/login",
        json={
            "email": record.employee.email,
            "password": record.password,
            "remember_me": False,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["employee"]["permissions"] == []
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_dashboard_reads_reject_missing_bearer_token_directly(
    client: TestClient,
    dashboard_factory: DashboardFixtureFactory,
):
    record = dashboard_factory.create()
    routes = [
        *READ_ROUTES,
        f"/api/v1/tasks/{quote(record.request_no, safe='')}/views/{quote(record.view_code, safe='')}",
    ]

    for route in routes:
        response = client.get(route)
        assert response.status_code == 401, (route, response.text)
        assert response.json()["detail"]["code"] == "UNAUTHORIZED"


def test_personal_dashboard_is_scoped_and_admin_overview_is_separate(
    client: TestClient,
    dashboard_factory: DashboardFixtureFactory,
):
    record = dashboard_factory.create()
    admin_headers = _login_as_admin(client)
    employee_headers = _login_as_fixture_employee(client, record)

    admin_dashboard = client.get("/api/v1/dashboard", headers=admin_headers)
    employee_dashboard = client.get("/api/v1/dashboard", headers=employee_headers)
    admin_overview = client.get("/api/v1/dashboard/overview", headers=admin_headers)
    admin_tasks = client.get(
        "/api/v1/dashboard/tasks",
        params={"page": 1, "page_size": 100},
        headers=admin_headers,
    )
    employee_tasks = client.get(
        "/api/v1/dashboard/tasks",
        params={"page": 1, "page_size": 100},
        headers=employee_headers,
    )
    admin_all_tasks = client.get(
        "/api/v1/dashboard/tasks",
        params={"scope": "all", "page": 1, "page_size": 100},
        headers=admin_headers,
    )
    employee_all_tasks = client.get(
        "/api/v1/dashboard/tasks",
        params={"scope": "all", "page": 1, "page_size": 100},
        headers=employee_headers,
    )
    admin_lookup = client.get("/api/v1/dashboard/task-lookup", headers=admin_headers)
    employee_lookup = client.get("/api/v1/dashboard/task-lookup", headers=employee_headers)
    view_path = f"/api/v1/tasks/{quote(record.request_no, safe='')}/views/{quote(record.view_code, safe='')}"
    admin_view = client.get(view_path, headers=admin_headers)
    employee_view = client.get(view_path, headers=employee_headers)

    for response in (
        admin_dashboard,
        employee_dashboard,
        admin_overview,
        admin_tasks,
        employee_tasks,
        admin_all_tasks,
        admin_lookup,
        employee_lookup,
        admin_view,
        employee_view,
    ):
        assert response.status_code == 200, response.text
    assert admin_dashboard.json()["scope"] == employee_dashboard.json()["scope"] == "mine"
    assert admin_dashboard.json()["priority_actions"] == []
    assert employee_dashboard.json()["summary"]["total_count"] == 1
    assert admin_overview.json()["scope"] == "all"
    assert record.request_no in {item["request_no"] for item in admin_all_tasks.json()["items"]}
    assert employee_tasks.json()["scope"] == "mine"
    assert employee_all_tasks.status_code == 403
    assert admin_lookup.json() == employee_lookup.json()
    assert admin_view.json() == employee_view.json() == {
        "request_no": record.request_no,
        "request_title": record.data_request.title,
        "view_code": record.view_code,
        "payload": record.snapshot.payload,
    }


def test_structured_task_view_not_found_error_remains_detail_code_contract(
    client: TestClient,
    dashboard_factory: DashboardFixtureFactory,
):
    headers = _login_as_admin(client)
    record = dashboard_factory.create()

    response = client.get(
        f"/api/v1/tasks/{quote(record.request_no, safe='')}/views/MISSING_VIEW",
        headers=headers,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "TASK_VIEW_NOT_FOUND"
    assert response.json()["detail"]["message"]


def test_shared_client_owns_one_refresh_then_login_redirect_contract():
    """D-14 stays a Frontend_2/src/shared/api.ts transport concern."""

    shared_api = Path(__file__).parents[2] / "Frontend_2/src/shared/api.ts"
    if not shared_api.exists():
        pytest.skip("Frontend source is not mounted in the backend test container")
    source = shared_api.read_text(encoding="utf-8")
    assert "if (response.status === 401 && !retried" in source
    assert "await refreshAccessToken()" in source
    assert "window.location.assign('/login')" in source
