"""Wave 1 specifications for dashboard and task-list response shape."""

from fastapi.testclient import TestClient
from urllib.parse import quote

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
LEGACY_DASHBOARD_KEYS = {
    "stat_cards",
    "warning_cards",
    "preferred_items",
    "supplement_items",
    "task_rows",
}
TASK_ITEM_KEYS = {
    "request_no",
    "client",
    "title",
    "assignee_code",
    "assignee_name",
    "stage_code",
    "stage_group_code",
    "stage_label",
    "status_code",
    "status_group_code",
    "priority_code",
    "decision_status",
    "requires_action",
    "detail_route",
    "created_at",
    "updated_at",
}
UNKNOWN_DETAIL_ROUTE = "/dashboard/tasks?stage=UNKNOWN"


def _find_item(items: list[dict], request_no: str) -> dict:
    return next(item for item in items if item["request_no"] == request_no)


def test_dashboard_and_task_list_have_exact_new_envelopes_and_empty_success(
    client: TestClient,
    dashboard_factory: DashboardFixtureFactory,
):
    dashboard_factory.create(
        pipeline_status="WAITING_REQUIREMENT_REVIEW",
        current_stage="REQUIREMENT_ANALYSIS",
        stage_code="REQUIREMENT_ANALYSIS",
    )
    headers = _login_as_admin(client)

    dashboard_response = client.get("/api/v1/dashboard", headers=headers)
    assert dashboard_response.status_code == 200, dashboard_response.text
    dashboard = dashboard_response.json()
    assert set(dashboard) == {
        "scope",
        "summary",
        "progress",
        "generated_at",
        "priority_cards",
        "priority_actions",
        "popular_products",
        "popular_products_unavailable_message",
        "approval_tasks",
        "deadline_tasks",
        "active_task_count",
    }
    assert not LEGACY_DASHBOARD_KEYS & set(dashboard)
    assert dashboard["popular_products"] == []
    assert dashboard["popular_products_unavailable_message"] == "인기 상품 데이터는 제공되지 않습니다."
    assert dashboard["scope"] == "mine"
    assert len(dashboard["priority_actions"]) <= 5
    assert len(dashboard["approval_tasks"]) <= 5
    assert len(dashboard["deadline_tasks"]) <= 5

    task_response = client.get(
        "/api/v1/dashboard/tasks",
        params={"priority": "FINAL", "stage": "UNKNOWN", "page": 1, "page_size": 30},
        headers=headers,
    )
    assert task_response.status_code == 200, task_response.text
    task_list = task_response.json()
    assert set(task_list) == {"scope", "items", "total_count", "page", "page_size"}
    assert task_list["scope"] == "mine"
    assert task_list["items"] == []
    assert task_list["total_count"] == 0
    assert task_list["page"] == 1
    assert task_list["page_size"] == 30


def test_contract_separates_codes_labels_and_allow_listed_detail_routes(
    client: TestClient,
    dashboard_factory: DashboardFixtureFactory,
):
    requirement = dashboard_factory.create(
        pipeline_status="RUNNING",
        current_stage="REQUIREMENT_ANALYSIS",
        stage_code="REQUIREMENT_ANALYSIS",
    )
    sample = dashboard_factory.create(
        pipeline_status="RUNNING",
        current_stage="DATA_SELECTION",
        stage_code="DATA_SELECTION",
    )
    final = dashboard_factory.create(
        pipeline_status="RUNNING",
        current_stage="DATA_PROCESSING",
        stage_code="DATA_PROCESSING",
    )
    completed = dashboard_factory.create(
        pipeline_status="COMPLETED",
        current_stage="COMPLETED",
        stage_code="COMPLETED",
        stage_status="COMPLETED",
    )
    unknown = dashboard_factory.create(include_pipeline=False, include_stage=False)
    headers = _login_as_admin(client)

    dashboard = client.get("/api/v1/dashboard", headers=headers)
    tasks = client.get(
        "/api/v1/dashboard/tasks",
        params={"scope": "all", "page": 1, "page_size": 100},
        headers=headers,
    )
    assert dashboard.status_code == 200, dashboard.text
    assert tasks.status_code == 200, tasks.text

    expected_groups = {
        requirement.request_no: "REQUIREMENT_ANALYSIS",
        sample.request_no: "SAMPLE_DATA",
        final.request_no: "FINAL_OUTPUT",
        completed.request_no: "COMPLETED",
        unknown.request_no: "UNKNOWN",
    }
    for request_no, expected_group in expected_groups.items():
        item = _find_item(tasks.json()["items"], request_no)
        assert TASK_ITEM_KEYS <= set(item)
        assert item["stage_group_code"] == expected_group
        if expected_group == "UNKNOWN":
            assert item["detail_route"] == UNKNOWN_DETAIL_ROUTE
            assert item["stage_label"] == "상태 확인 필요"
        else:
            assert item["run_id"] is not None
            expected_route = (
                f"/tasks/{quote(request_no, safe='')}/runs/{item['run_id']}/detail"
            )
            assert item["detail_route"] == expected_route
        assert item["stage_group_code"] in STAGE_GROUP_CODES
        assert item["priority_code"] is None or item["priority_code"] in PRIORITY_CODES
        assert item["stage_label"] != item["stage_group_code"]
        assert item["detail_route"] != item["stage_group_code"]

    for card in dashboard.json()["priority_cards"]:
        assert card["priority_code"] in PRIORITY_CODES
        assert card["label"]
        assert card["label"] != card["priority_code"]
        assert card["detail_route"] == f"/dashboard/tasks?priority={card['priority_code']}"


def test_contract_exposes_canonical_code_families_on_dashboard_collections(
    client: TestClient,
    dashboard_factory: DashboardFixtureFactory,
):
    dashboard_factory.create(
        pipeline_status="WAITING_REQUIREMENT_REVIEW",
        current_stage="REQUIREMENT_ANALYSIS",
        stage_code="REQUIREMENT_ANALYSIS",
    )
    body = client.get(
        "/api/v1/dashboard",
        headers=_login_as_admin(client),
    ).json()

    for item in body["priority_actions"] + body["approval_tasks"]:
        assert item["priority_code"] in PRIORITY_CODES
        assert item["stage_group_code"] in STAGE_GROUP_CODES
        assert item["decision_status"]
        assert item["stage_label"]
        assert item["detail_route"].startswith("/tasks/") or item["detail_route"] == UNKNOWN_DETAIL_ROUTE
