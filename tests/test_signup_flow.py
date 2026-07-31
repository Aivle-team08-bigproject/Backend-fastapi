"""회원가입 → 관리자 승인/거절 → 로그인 흐름 검증."""

from tests.conftest import BOOTSTRAP_ADMIN_EMAIL, BOOTSTRAP_ADMIN_PASSWORD, department_id

VALID_PASSWORD = "Signup!2026Secure"


def _login_as_admin(client) -> dict:
    from tests.test_auth_flow import _login_as_admin as helper

    return helper(client)


def _signup_payload(client, email: str, **overrides) -> dict:
    payload = {
        "name": "김신입",
        "email": email,
        "phone": "010-1234-5678",
        "department_id": department_id(client),
        "position": "STAFF",
        "password": VALID_PASSWORD,
        "terms_agreed": True,
        "privacy_agreed": True,
    }
    payload.update(overrides)
    return payload


def test_signup_creates_pending_account(client):
    response = client.post("/api/auth/signup", json=_signup_payload(client, "new.hire.001@company.com"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "PENDING_APPROVAL"
    assert body["email"] == "new.hire.001@company.com"
    assert body["employee_code"].startswith("SU-")


def test_signup_rejects_disallowed_email_domain(client):
    response = client.post("/api/auth/signup", json=_signup_payload(client, "someone@gmail.com"))
    assert response.status_code == 422


def test_signup_rejects_duplicate_email(client):
    client.post("/api/auth/signup", json=_signup_payload(client, "dup.hire@company.com"))
    duplicate = client.post("/api/auth/signup", json=_signup_payload(client, "dup.hire@company.com"))
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "EMAIL_ALREADY_REGISTERED"


def test_signup_rejects_missing_consent(client):
    response = client.post(
        "/api/auth/signup", json=_signup_payload(client, "no.consent@company.com", privacy_agreed=False)
    )
    assert response.status_code == 422


def test_signup_rejects_invalid_phone(client):
    response = client.post(
        "/api/auth/signup", json=_signup_payload(client, "bad.phone@company.com", phone="123-456")
    )
    assert response.status_code == 422


def test_signup_rejects_unknown_department(client):
    response = client.post(
        "/api/auth/signup", json=_signup_payload(client, "bad.dept@company.com", department_id=999999)
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "DEPARTMENT_NOT_FOUND"


def test_pending_account_cannot_login(client):
    client.post("/api/auth/signup", json=_signup_payload(client, "pending.login@company.com"))
    login = client.post(
        "/api/auth/login",
        json={"email": "pending.login@company.com", "password": VALID_PASSWORD, "remember_me": False},
    )
    assert login.status_code == 403
    assert login.json()["detail"]["code"] == "SIGNUP_PENDING_APPROVAL"


def test_approve_flow_grants_access(client):
    signup = client.post("/api/auth/signup", json=_signup_payload(client, "approve.me@company.com"))
    employee_code = signup.json()["employee_code"]

    admin_headers = _login_as_admin(client)

    pending = client.get("/api/admin/employees/signup-requests", headers=admin_headers)
    assert pending.status_code == 200
    assert any(item["employee_code"] == employee_code for item in pending.json())

    approved = client.post(
        f"/api/admin/employees/signup-requests/{employee_code}/approve",
        headers=admin_headers,
        json={"role": "GENERAL", "department_id": department_id(client), "position": "STAFF"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "ACTIVE"
    assert approved.json()["role"] == "GENERAL"
    assert set(approved.json()["permissions"]) == {"DATA_PRODUCT_READ", "QUOTE_READ"}

    login = client.post(
        "/api/auth/login",
        json={"email": "approve.me@company.com", "password": VALID_PASSWORD, "remember_me": False},
    )
    assert login.status_code == 200
    assert login.json()["employee"]["status"] == "ACTIVE"


def test_reject_flow_blocks_login(client):
    signup = client.post("/api/auth/signup", json=_signup_payload(client, "reject.me@company.com"))
    employee_code = signup.json()["employee_code"]

    admin_headers = _login_as_admin(client)

    rejected = client.post(
        f"/api/admin/employees/signup-requests/{employee_code}/reject",
        headers=admin_headers,
        json={"reason": "부서 정보가 확인되지 않음"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"

    login = client.post(
        "/api/auth/login",
        json={"email": "reject.me@company.com", "password": VALID_PASSWORD, "remember_me": False},
    )
    assert login.status_code == 403
    assert login.json()["detail"]["code"] == "SIGNUP_REJECTED"


def test_signup_after_rejection_is_allowed(client):
    """거절된 이메일은 재신청을 허용한다 — employee_code는 유지된 채 PENDING_APPROVAL로 되돌아간다."""
    signup = client.post("/api/auth/signup", json=_signup_payload(client, "retry.after.reject@company.com"))
    employee_code = signup.json()["employee_code"]

    admin_headers = _login_as_admin(client)
    rejected = client.post(
        f"/api/admin/employees/signup-requests/{employee_code}/reject",
        headers=admin_headers,
        json={"reason": "직급 정보 확인 불가"},
    )
    assert rejected.status_code == 200

    retry = client.post(
        "/api/auth/signup",
        json=_signup_payload(client, "retry.after.reject@company.com", name="김재신청"),
    )
    assert retry.status_code == 200, retry.text
    body = retry.json()
    assert body["status"] == "PENDING_APPROVAL"
    assert body["employee_code"] == employee_code  # 같은 행을 재사용 — 감사 로그 이력이 끊기지 않음

    approved = client.post(
        f"/api/admin/employees/signup-requests/{employee_code}/approve",
        headers=admin_headers,
        json={"role": "GENERAL"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["rejected_reason"] is None
    assert approved.json()["name"] == "김재신청"


def test_signup_blocked_while_pending_or_active(client):
    """PENDING_APPROVAL/ACTIVE 상태인 이메일은 여전히 재신청이 막혀야 한다 (REJECTED만 예외)."""
    client.post("/api/auth/signup", json=_signup_payload(client, "still.pending@company.com"))
    duplicate = client.post("/api/auth/signup", json=_signup_payload(client, "still.pending@company.com"))
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "EMAIL_ALREADY_REGISTERED"


def test_non_admin_cannot_approve_signup(client):
    signup_admin_headers = _login_as_admin(client)

    # 승인/거절 권한이 없는 일반 직원 계정을 만든다.
    created = client.post(
        "/api/admin/employees",
        headers=signup_admin_headers,
        json={
            "employee_code": "HANA-NOPERM-001",
            "name": "권한없음",
            "email": "no-perm@company.com",
            "department_id": department_id(client),
            "permissions": ["DATA_PRODUCT_READ"],
        },
    )
    temp_password = created.json()["temporary_password"]

    login = client.post(
        "/api/auth/login",
        json={"email": "no-perm@company.com", "password": temp_password, "remember_me": False},
    )
    no_perm_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    signup = client.post("/api/auth/signup", json=_signup_payload(client, "blocked.approve@company.com"))
    employee_code = signup.json()["employee_code"]

    forbidden = client.post(
        f"/api/admin/employees/signup-requests/{employee_code}/approve",
        headers=no_perm_headers,
        json={"role": "GENERAL"},
    )
    assert forbidden.status_code == 403


def test_signup_response_never_contains_password_hash(client):
    response = client.post("/api/auth/signup", json=_signup_payload(client, "no.hash.leak@company.com"))
    assert "password" not in response.text
    assert "password_hash" not in response.text
