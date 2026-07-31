"""Wave 0 HTTP contract tracer for authenticated dashboard reads."""

from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

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


def test_authenticated_dashboard_and_task_list_contract(
    client: TestClient,
    dashboard_factory: DashboardFixtureFactory,
):
    record = dashboard_factory.create(
        pipeline_status="WAITING_REQUIREMENT_REVIEW",
        current_stage="REQUIREMENT_ANALYSIS",
        stage_code="REQUIREMENT_ANALYSIS",
        review_type="REQUIREMENT",
    )
    headers = _login_as_admin(client)

    dashboard_response = client.get("/api/v1/dashboard", headers=headers)

    assert dashboard_response.status_code == 200, dashboard_response.text
    dashboard = dashboard_response.json()
    assert {
        "generated_at",
        "priority_cards",
        "priority_actions",
        "popular_products",
        "popular_products_unavailable_message",
        "approval_tasks",
        "deadline_tasks",
        "active_task_count",
    } <= dashboard.keys()
    assert not LEGACY_DASHBOARD_KEYS & dashboard.keys()
    assert dashboard["popular_products"] == []
    assert dashboard["popular_products_unavailable_message"] == "인기 상품 데이터는 제공되지 않습니다."

    for card in dashboard["priority_cards"]:
        assert card["priority_code"] in PRIORITY_CODES
    for item in dashboard["priority_actions"] + dashboard["approval_tasks"]:
        assert item["request_no"]
        assert item["priority_code"] in PRIORITY_CODES
        assert item["stage_group_code"] in STAGE_GROUP_CODES

    task_response = client.get(
        "/api/v1/dashboard/tasks",
        params={
            "priority": "REQUIREMENT",
            "stage": "REQUIREMENT_ANALYSIS",
            "page": 1,
            "page_size": 30,
        },
        headers=headers,
    )

    assert task_response.status_code == 200, task_response.text
    task_list = task_response.json()
    assert set(task_list) == {"items", "total_count", "page", "page_size"}
    assert task_list["page"] == 1
    assert task_list["page_size"] == 30
    assert task_list["total_count"] >= len(task_list["items"])
    for item in task_list["items"]:
        assert {
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
        } <= item.keys()
        assert item["priority_code"] in PRIORITY_CODES
        assert item["stage_group_code"] in STAGE_GROUP_CODES


def test_dashboard_popular_products_are_counted_from_request_metadata(
    client: TestClient,
    dashboard_factory: DashboardFixtureFactory,
):
    dashboard_factory.create(analysis_condition={"product_name": "카드 승인 데이터"})
    dashboard_factory.create(analysis_condition={"product_name": "카드 승인 데이터"})
    dashboard_factory.create(analysis_condition={"product_name": "가맹점 매출 데이터"})

    response = client.get("/api/v1/dashboard", headers=_login_as_admin(client))

    assert response.status_code == 200, response.text
    products = response.json()["popular_products"]
    assert products[0] == {
        "product_code": "PRODUCT-001",
        "product_name": "카드 승인 데이터",
        "request_count": 2,
    }
    assert products[1] == {
        "product_code": "PRODUCT-002",
        "product_name": "가맹점 매출 데이터",
        "request_count": 1,
    }
    assert response.json()["popular_products_unavailable_message"] == ""


def test_dashboard_deadline_tasks_exclude_completed_work_and_sort_by_due_at(
    client: TestClient,
    dashboard_factory: DashboardFixtureFactory,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    overdue = dashboard_factory.create(
        analysis_condition={"due_at": (now - timedelta(hours=1)).isoformat()},
    )
    imminent = dashboard_factory.create(
        analysis_condition={"due_at": (now + timedelta(hours=3)).isoformat()},
    )
    completed = dashboard_factory.create(
        pipeline_status="COMPLETED",
        current_stage="COMPLETED",
        stage_code="COMPLETED",
        stage_status="COMPLETED",
        analysis_condition={"due_at": (now - timedelta(days=1)).isoformat()},
    )

    response = client.get("/api/v1/dashboard", headers=_login_as_admin(client))

    assert response.status_code == 200, response.text
    deadline_tasks = response.json()["deadline_tasks"]
    request_nos = [item["request_no"] for item in deadline_tasks]
    assert request_nos[:2] == [overdue.request_no, imminent.request_no]
    assert completed.request_no not in request_nos
    assert all(item["due_at"] for item in deadline_tasks)


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page": -1},
        {"page_size": 29},
        {"page_size": 31},
        {"page_size": 200},
    ],
)
def test_task_list_rejects_invalid_page_and_page_size(client: TestClient, params):
    headers = _login_as_admin(client)

    response = client.get("/api/v1/dashboard/tasks", params=params, headers=headers)

    assert response.status_code == 422


@pytest.mark.parametrize("page_size", [30, 50, 100])
def test_task_list_accepts_allow_listed_page_sizes(client: TestClient, page_size: int):
    headers = _login_as_admin(client)

    response = client.get(
        "/api/v1/dashboard/tasks",
        params={"page": 1, "page_size": page_size},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["page_size"] == page_size


def test_task_list_empty_filter_is_a_success_response(client: TestClient):
    headers = _login_as_admin(client)

    response = client.get(
        "/api/v1/dashboard/tasks",
        params={"priority": "FINAL", "stage": "UNKNOWN", "page": 1, "page_size": 30},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == []
    assert body["total_count"] == 0
    assert body["page"] == 1
    assert body["page_size"] == 30


def test_equal_created_at_rows_are_ordered_by_request_no_descending(
    client: TestClient,
    dashboard_factory: DashboardFixtureFactory,
):
    timestamp = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    order_marker = uuid4().hex[:8]
    older_request = dashboard_factory.create(
        created_at=timestamp,
        request_no=f"REQ/{order_marker}-A",
        pipeline_status="WAITING_REQUIREMENT_REVIEW",
        current_stage="REQUIREMENT_ANALYSIS",
        stage_code="REQUIREMENT_ANALYSIS",
    )
    newer_request_no = f"REQ/{order_marker}-Z"
    newer_request = dashboard_factory.create(
        created_at=timestamp,
        request_no=newer_request_no,
        pipeline_status="WAITING_REQUIREMENT_REVIEW",
        current_stage="REQUIREMENT_ANALYSIS",
        stage_code="REQUIREMENT_ANALYSIS",
    )
    headers = _login_as_admin(client)

    response = client.get(
        "/api/v1/dashboard/tasks",
        params={"priority": "REQUIREMENT", "stage": "REQUIREMENT_ANALYSIS"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    request_numbers = [item["request_no"] for item in response.json()["items"]]
    assert request_numbers.index(newer_request.request_no) < request_numbers.index(older_request.request_no)


def test_task_view_returns_snapshot_payload_with_encoded_path_values(
    client: TestClient,
    dashboard_factory: DashboardFixtureFactory,
):
    path_marker = uuid4().hex[:8]
    record = dashboard_factory.create(
        request_no=f"REQ/encoded {path_marker}",
        view_code=f"VIEW/encoded {path_marker}",
        snapshot_payload={"unchanged": True, "nested": {"marker": "encoded"}},
    )
    headers = _login_as_admin(client)
    encoded_request_no = quote(record.request_no, safe="")
    encoded_view_code = quote(record.view_code, safe="")

    response = client.get(
        f"/api/v1/tasks/{encoded_request_no}/views/{encoded_view_code}",
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "request_no": record.request_no,
        "request_title": record.data_request.title,
        "view_code": record.view_code,
        "payload": record.snapshot.payload,
    }
