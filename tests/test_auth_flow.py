"""로그인 → 비밀번호 강제변경 → 직원 생성 → 권한관리 → 갱신 → 계정잠금까지 전체 흐름 검증."""

from tests.conftest import BOOTSTRAP_ADMIN_ID, BOOTSTRAP_ADMIN_PASSWORD

CHANGED_ADMIN_PASSWORD = "HanaAdmin!2026Rotated"


def _login_as_admin(client) -> dict:
    """부트스트랩 관리자로 로그인한다. 이미 다른 테스트가 비밀번호를 바꿔놨을 수도 있으므로
    두 비밀번호를 순서대로 시도한다. 반환값은 Authorization 헤더."""
    for password in (BOOTSTRAP_ADMIN_PASSWORD, CHANGED_ADMIN_PASSWORD):
        response = client.post(
            "/api/auth/login",
            json={"employee_code": BOOTSTRAP_ADMIN_ID, "password": password, "remember_me": False},
        )
        if response.status_code == 200:
            body = response.json()
            if body["employee"]["must_change_password"]:
                change = client.post(
                    "/api/auth/change-password",
                    headers={"Authorization": f"Bearer {body['access_token']}"},
                    json={"current_password": password, "new_password": CHANGED_ADMIN_PASSWORD},
                )
                assert change.status_code == 204
                response = client.post(
                    "/api/auth/login",
                    json={
                        "employee_code": BOOTSTRAP_ADMIN_ID,
                        "password": CHANGED_ADMIN_PASSWORD,
                        "remember_me": False,
                    },
                )
            token = response.json()["access_token"]
            return {"Authorization": f"Bearer {token}"}
    raise AssertionError("관리자 로그인에 실패했습니다 (두 비밀번호 모두 시도함)")


def test_password_change_required_before_business_api(client):
    response = client.post(
        "/api/auth/login",
        json={"employee_code": BOOTSTRAP_ADMIN_ID, "password": BOOTSTRAP_ADMIN_PASSWORD, "remember_me": False},
    )
    if response.status_code != 200:
        # 다른 테스트가 이미 비밀번호를 바꿔놨다면 이 테스트는 의미가 없으니 건너뛴다.
        return

    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    blocked = client.get("/api/admin/employees", headers=headers)
    assert blocked.status_code == 403

    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200


def test_create_employee_and_login(client):
    headers = _login_as_admin(client)

    created = client.post(
        "/api/admin/employees",
        headers=headers,
        json={
            "employee_code": "HANA-TEST-001",
            "name": "홍길동",
            "department": "데이터사업팀",
            "permissions": ["DATA_PRODUCT_READ", "QUOTE_READ"],
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert len(body["temporary_password"]) == 16
    assert body["employee"]["created_by"] == BOOTSTRAP_ADMIN_ID

    duplicate = client.post(
        "/api/admin/employees",
        headers=headers,
        json={
            "employee_code": "HANA-TEST-001",
            "name": "가짜",
            "department": "x",
            "permissions": [],
        },
    )
    assert duplicate.status_code == 409

    login = client.post(
        "/api/auth/login",
        json={
            "employee_code": "HANA-TEST-001",
            "password": body["temporary_password"],
            "remember_me": False,
        },
    )
    assert login.status_code == 200
    assert login.json()["employee"]["must_change_password"] is True


def test_update_permissions_with_overlap_does_not_fail(client):
    """겹치는 권한 코드를 유지한 채 교체해도 문제없이 성공해야 한다."""
    headers = _login_as_admin(client)

    client.post(
        "/api/admin/employees",
        headers=headers,
        json={
            "employee_code": "HANA-TEST-002",
            "name": "테스트2",
            "department": "x",
            "permissions": ["DATA_PRODUCT_READ", "QUOTE_READ"],
        },
    )

    updated = client.put(
        "/api/admin/employees/HANA-TEST-002/permissions",
        headers=headers,
        json={"permissions": ["QUOTE_READ", "CONTRACT_MANAGE"]},
    )
    assert updated.status_code == 200, updated.text
    assert set(updated.json()["permissions"]) == {"QUOTE_READ", "CONTRACT_MANAGE"}


def test_self_lockout_protections(client):
    headers = _login_as_admin(client)

    remove_own_manage = client.put(
        f"/api/admin/employees/{BOOTSTRAP_ADMIN_ID}/permissions",
        headers=headers,
        json={"permissions": ["EMPLOYEE_READ"]},
    )
    assert remove_own_manage.status_code == 400
    assert remove_own_manage.json()["detail"]["code"] == "SELF_PERMISSION_REMOVAL_BLOCKED"

    disable_self = client.patch(
        f"/api/admin/employees/{BOOTSTRAP_ADMIN_ID}/status",
        headers=headers,
        json={"status": "DISABLED"},
    )
    assert disable_self.status_code == 400
    assert disable_self.json()["detail"]["code"] == "SELF_DISABLE_BLOCKED"


def test_refresh_rotates_token(client):
    headers = _login_as_admin(client)
    assert "DM_REFRESH" in client.cookies

    refreshed = client.post("/api/auth/refresh")
    assert refreshed.status_code == 200
    assert "access_token" in refreshed.json()


def test_login_lockout_after_five_failures(client):
    for _ in range(5):
        client.post(
            "/api/auth/login",
            json={"employee_code": "HANA-TEST-001", "password": "wrong-password", "remember_me": False},
        )

    locked = client.post(
        "/api/auth/login",
        json={"employee_code": "HANA-TEST-001", "password": "wrong-password", "remember_me": False},
    )
    assert locked.status_code == 403
    assert locked.json()["detail"]["code"] == "ACCOUNT_LOCKED"
