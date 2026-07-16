"""7,8,9번 항목(감사 로그 / 마지막 관리자 보호 / 72바이트 비밀번호)에 대한 회귀 테스트."""

from tests.conftest import BOOTSTRAP_ADMIN_ID
from tests.test_auth_flow import _login_as_admin


def _create_and_activate_employee(client, headers, employee_code: str, permissions: list[str]) -> dict:
    """직원을 만들고 임시 비밀번호로 로그인 + 비밀번호 변경까지 마쳐서 실제 권한이 적용된
    Authorization 헤더를 반환한다."""
    created = client.post(
        "/api/admin/employees",
        headers=headers,
        json={
            "employee_code": employee_code,
            "name": "테스트직원",
            "department": "x",
            "permissions": permissions,
        },
    )
    assert created.status_code == 200, created.text
    temp_password = created.json()["temporary_password"]

    login = client.post(
        "/api/auth/login",
        json={"employee_code": employee_code, "password": temp_password, "remember_me": False},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    new_password = "NewEmployee!2026Pw"
    changed = client.post(
        "/api/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": temp_password, "new_password": new_password},
    )
    assert changed.status_code == 204

    relogin = client.post(
        "/api/auth/login",
        json={"employee_code": employee_code, "password": new_password, "remember_me": False},
    )
    assert relogin.status_code == 200
    return {"Authorization": f"Bearer {relogin.json()['access_token']}"}


def test_last_admin_protection_blocks_disabling_only_permission_manager(client):
    """EMPLOYEE_PERMISSION_MANAGE를 가진 유일한 활성 직원(부트스트랩 관리자)을,
    그 권한이 없는 다른 직원(EMPLOYEE_UPDATE만 보유)이 비활성화하려는 시도는 막혀야 한다."""
    admin_headers = _login_as_admin(client)

    # EMPLOYEE_PERMISSION_MANAGE 없이 EMPLOYEE_UPDATE만 가진 직원을 하나 만든다.
    limited_headers = _create_and_activate_employee(
        client, admin_headers, "HANA-LIMITED-001", ["EMPLOYEE_UPDATE"]
    )

    attempt = client.patch(
        f"/api/admin/employees/{BOOTSTRAP_ADMIN_ID}/status",
        headers=limited_headers,
        json={"status": "DISABLED"},
    )
    assert attempt.status_code == 400, attempt.text
    assert attempt.json()["detail"]["code"] == "LAST_PERMISSION_MANAGER_BLOCKED"

    # 관리자 계정은 여전히 ACTIVE여야 한다.
    check = client.get(f"/api/admin/employees/{BOOTSTRAP_ADMIN_ID}", headers=admin_headers)
    assert check.status_code == 200
    assert check.json()["status"] == "ACTIVE"


def test_last_admin_protection_allows_when_another_manager_exists(client):
    """EMPLOYEE_PERMISSION_MANAGE를 가진 다른 직원이 있으면, 그중 한 명을 비활성화하는 건 허용돼야 한다."""
    admin_headers = _login_as_admin(client)

    created = client.post(
        "/api/admin/employees",
        headers=admin_headers,
        json={
            "employee_code": "HANA-COMANAGER-001",
            "name": "공동관리자",
            "department": "x",
            "permissions": ["EMPLOYEE_PERMISSION_MANAGE", "EMPLOYEE_UPDATE"],
        },
    )
    assert created.status_code == 200

    disable = client.patch(
        "/api/admin/employees/HANA-COMANAGER-001/status",
        headers=admin_headers,
        json={"status": "DISABLED"},
    )
    # 부트스트랩 관리자 본인이 여전히 EMPLOYEE_PERMISSION_MANAGE를 갖고 있으므로 차단되지 않아야 한다.
    assert disable.status_code == 200
    assert disable.json()["status"] == "DISABLED"


def test_audit_log_records_admin_actions(client):
    admin_headers = _login_as_admin(client)

    client.post(
        "/api/admin/employees",
        headers=admin_headers,
        json={
            "employee_code": "HANA-AUDIT-001",
            "name": "감사로그테스트",
            "department": "x",
            "permissions": ["DATA_PRODUCT_READ"],
        },
    )

    logs = client.get("/api/admin/employees/audit-logs", headers=admin_headers)
    assert logs.status_code == 200
    entries = logs.json()
    assert any(
        entry["action"] == "EMPLOYEE_CREATED" and entry["target_employee_code"] == "HANA-AUDIT-001"
        for entry in entries
    )
    assert all(entry["actor_employee_code"] for entry in entries)


def test_password_over_72_bytes_is_rejected_cleanly(client):
    """복잡도 조건은 만족하지만 한글 때문에 UTF-8 72바이트를 넘는 비밀번호는
    500이 아니라 422로 깔끔하게 거부돼야 한다."""
    headers = _login_as_admin(client)

    too_long_korean_password = "Aa1!" + ("한글비밀번호테스트" * 10)[:60]
    assert len(too_long_korean_password.encode("utf-8")) > 72

    from tests.test_auth_flow import CHANGED_ADMIN_PASSWORD

    response = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"current_password": CHANGED_ADMIN_PASSWORD, "new_password": too_long_korean_password},
    )
    assert response.status_code == 422
    assert response.status_code != 500
