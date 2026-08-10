from uuid import uuid4

import pytest

from app.common.errors import DomainException
from app.domains.notices.model import NoticeStatus
from app.domains.notices.service import _validate_status_transition
from tests.conftest import department_id
from tests.test_auth_flow import _login_as_admin, unique_employee_code


def _create_employee_headers(client, admin_headers):
    code = unique_employee_code("HANA-NOTICE")
    response = client.post(
        "/api/admin/employees",
        headers=admin_headers,
        json={
            "employee_code": code,
            "name": "공지 일반직원",
            "email": f"{code.lower()}@company.com",
            "department_id": department_id(client),
            "permissions": [],
        },
    )
    assert response.status_code == 200, response.text
    temporary = response.json()["temporary_password"]
    login = client.post(
        "/api/auth/login",
        json={"email": f"{code.lower()}@company.com", "password": temporary, "remember_me": False},
    )
    assert login.status_code == 200
    changed = client.post(
        "/api/auth/change-password",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        json={"current_password": temporary, "new_password": "NoticeEmployee!2026Pw"},
    )
    assert changed.status_code == 204
    relogin = client.post(
        "/api/auth/login",
        json={"email": f"{code.lower()}@company.com", "password": "NoticeEmployee!2026Pw", "remember_me": False},
    )
    assert relogin.status_code == 200
    return {"Authorization": f"Bearer {relogin.json()['access_token']}"}


def test_notice_public_contract_and_admin_only_writes(client):
    admin_headers = _login_as_admin(client)
    title = f"공지 테스트 {uuid4().hex[:8]}"
    created = client.post(
        "/api/v1/admin/notices",
        headers=admin_headers,
        json={"title": title, "content": "첫 번째 공지", "status": "PUBLISHED"},
    )
    assert created.status_code == 201, created.text
    notice_id = created.json()["id"]

    listed = client.get("/api/v1/notices", headers=admin_headers)
    assert listed.status_code == 200
    assert any(item["id"] == notice_id for item in listed.json()["items"])

    latest = client.get("/api/v1/notices/latest", headers=admin_headers)
    assert latest.status_code == 200
    assert latest.json()["item"]["id"] == notice_id

    detail = client.get(f"/api/v1/notices/{notice_id}", headers=admin_headers)
    assert detail.status_code == 200
    assert detail.json()["content"] == "첫 번째 공지"

    employee_headers = _create_employee_headers(client, admin_headers)
    blocked = client.post(
        "/api/v1/admin/notices",
        headers=employee_headers,
        json={"title": "권한 없음", "content": "차단되어야 함", "status": "PUBLISHED"},
    )
    assert blocked.status_code == 403
    blocked_delete = client.delete(f"/api/v1/admin/notices/{notice_id}", headers=employee_headers)
    assert blocked_delete.status_code == 403

    deleted = client.delete(f"/api/v1/admin/notices/{notice_id}", headers=admin_headers)
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/notices/{notice_id}", headers=admin_headers).status_code == 404


def test_notice_status_transition_rejects_published_to_draft(client):
    admin_headers = _login_as_admin(client)
    created = client.post(
        "/api/v1/admin/notices",
        headers=admin_headers,
        json={"title": "게시 공지", "content": "본문", "status": "PUBLISHED"},
    )
    assert created.status_code == 201, created.text

    updated = client.patch(
        f"/api/v1/admin/notices/{created.json()['id']}",
        headers=admin_headers,
        json={"status": "DRAFT"},
    )
    assert updated.status_code == 400


def test_notice_status_transition_rules():
    _validate_status_transition(NoticeStatus.DRAFT, NoticeStatus.PUBLISHED)
    _validate_status_transition(NoticeStatus.PUBLISHED, NoticeStatus.ARCHIVED)
    _validate_status_transition(NoticeStatus.ARCHIVED, NoticeStatus.PUBLISHED)

    for current, next_status in (
        (NoticeStatus.DRAFT, NoticeStatus.ARCHIVED),
        (NoticeStatus.PUBLISHED, NoticeStatus.DRAFT),
        (NoticeStatus.ARCHIVED, NoticeStatus.DRAFT),
    ):
        with pytest.raises(DomainException) as exc_info:
            _validate_status_transition(current, next_status)
        assert exc_info.value.status_code == 400
        assert exc_info.value.code == "NOTICE_STATUS_INVALID"


def test_notice_draft_and_republish_state_transitions(client):
    admin_headers = _login_as_admin(client)
    draft = client.post(
        "/api/v1/admin/notices",
        headers=admin_headers,
        json={"title": "초안 공지", "content": "본문"},
    )
    assert draft.status_code == 201, draft.text
    assert draft.json()["status"] == "DRAFT"
    assert draft.json()["published_at"] is None

    draft_id = draft.json()["id"]
    assert client.get(f"/api/v1/notices/{draft_id}", headers=admin_headers).status_code == 404
    assert client.delete(f"/api/v1/admin/notices/{draft_id}", headers=admin_headers).status_code == 400

    published = client.patch(
        f"/api/v1/admin/notices/{draft_id}",
        headers=admin_headers,
        json={"status": "PUBLISHED"},
    )
    assert published.status_code == 200, published.text
    published_at = published.json()["published_at"]
    assert published.json()["status"] == "PUBLISHED"
    assert published_at is not None

    archived = client.delete(f"/api/v1/admin/notices/{draft_id}", headers=admin_headers)
    assert archived.status_code == 204
    archived_detail = client.get(f"/api/v1/admin/notices/{draft_id}", headers=admin_headers)
    assert archived_detail.json()["status"] == "ARCHIVED"
    assert archived_detail.json()["published_at"] == published_at

    republished = client.patch(
        f"/api/v1/admin/notices/{draft_id}",
        headers=admin_headers,
        json={"status": "PUBLISHED"},
    )
    assert republished.status_code == 200, republished.text
    assert republished.json()["status"] == "PUBLISHED"
    assert republished.json()["published_at"] == published_at


def test_notice_requires_authentication(client):
    assert client.get("/api/v1/notices").status_code == 401
    assert client.get("/api/v1/notices/latest").status_code == 401
    assert client.post(
        "/api/v1/admin/notices",
        json={"title": "인증 없음", "content": "차단되어야 함", "status": "PUBLISHED"},
    ).status_code == 401
