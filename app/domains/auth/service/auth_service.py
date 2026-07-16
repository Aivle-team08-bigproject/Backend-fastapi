import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import security
from app.common.time_utils import utcnow
from app.core.config import settings
from app.common.errors import bad_request, forbidden, not_found, unauthorized
from app.domains.auth.model.session_model import LoginSession
from app.domains.auth.schema.auth_schema import ChangePasswordRequest, LoginRequest
from app.domains.employees.model.employee_model import Employee, EmployeeStatus

MAX_LOGIN_FAILURES = 5
LOCK_MINUTES = 15


@dataclass
class LoginResult:
    employee: Employee
    session: LoginSession
    raw_refresh_token: str
    access_token: str
    access_token_expires_at: datetime


@dataclass
class RefreshResult:
    session: LoginSession
    raw_refresh_token: str
    access_token: str
    access_token_expires_at: datetime


def _absolute_ttl(remember_me: bool) -> timedelta:
    if remember_me:
        return timedelta(hours=settings.session_remember_me_ttl_hours)
    return timedelta(minutes=settings.session_normal_ttl_minutes)


def _invalid_credentials():
    return unauthorized("INVALID_CREDENTIALS", "직원 ID 또는 비밀번호가 올바르지 않습니다.")


async def _find_employee(db: AsyncSession, employee_code: str) -> Employee:
    result = await db.execute(
        select(Employee)
        .options(selectinload(Employee.permissions))
        .where(Employee.employee_code == employee_code)
    )
    employee = result.scalar_one_or_none()
    if employee is None:
        raise not_found("EMPLOYEE_NOT_FOUND", "직원 계정을 찾을 수 없습니다.")
    return employee


async def _enforce_concurrent_session_limit(db: AsyncSession, employee_id: int, now: datetime) -> None:
    result = await db.execute(
        select(LoginSession)
        .where(LoginSession.employee_id == employee_id, LoginSession.revoked_at.is_(None))
        .order_by(LoginSession.created_at.asc())
    )
    sessions = list(result.scalars().all())

    active: list[LoginSession] = []
    for session in sessions:
        if session.expires_at <= now:
            session.revoked_at = now
            session.revoke_reason = "만료 세션 정리"
        else:
            active.append(session)

    max_sessions = max(1, settings.max_active_sessions)
    revoke_count = max(0, len(active) - max_sessions + 1)
    for session in active[:revoke_count]:
        session.revoked_at = now
        session.revoke_reason = "동시 로그인 수 초과"


async def login(
    db: AsyncSession,
    payload: LoginRequest,
    ip_address: str | None,
    user_agent: str | None,
) -> LoginResult:
    result = await db.execute(
        select(Employee)
        .options(selectinload(Employee.permissions))
        .where(Employee.employee_code == payload.employee_code)
    )
    employee = result.scalar_one_or_none()
    if employee is None:
        raise _invalid_credentials()

    now = utcnow()

    if employee.status == EmployeeStatus.DISABLED:
        raise forbidden("ACCOUNT_DISABLED", "사용이 중지된 직원 계정입니다. 관리자에게 문의해주세요.")

    is_temporarily_locked = employee.locked_until is not None and employee.locked_until > now
    if is_temporarily_locked or employee.status == EmployeeStatus.LOCKED:
        raise forbidden("ACCOUNT_LOCKED", "로그인이 잠긴 계정입니다. 잠시 후 다시 시도하거나 관리자에게 문의해주세요.")

    if not security.verify_password(payload.password, employee.password_hash):
        employee.failed_login_count += 1
        if employee.failed_login_count >= MAX_LOGIN_FAILURES:
            employee.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            employee.failed_login_count = 0
        employee.updated_at = now
        await db.commit()
        raise _invalid_credentials()

    employee.failed_login_count = 0
    employee.locked_until = None
    employee.updated_at = now

    await _enforce_concurrent_session_limit(db, employee.id, now)

    raw_refresh_token = security.generate_raw_refresh_token()
    ttl = _absolute_ttl(payload.remember_me)

    session = LoginSession(
        id=uuid.uuid4(),
        employee_id=employee.id,
        refresh_token_hash=security.hash_refresh_token(raw_refresh_token),
        remember_me=payload.remember_me,
        ip_address=ip_address,
        user_agent=(user_agent or "")[:500] or None,
        created_at=now,
        last_seen_at=now,
        expires_at=now + ttl,
    )
    db.add(session)
    await db.flush()  # session.id 확정 + FK 제약 확인

    access_token, access_token_expires_at = security.create_access_token(
        employee_code=employee.employee_code,
        session_id=session.id,
        auth_version=employee.auth_version,
        name=employee.name,
        department=employee.department,
    )

    await db.commit()

    return LoginResult(
        employee=employee,
        session=session,
        raw_refresh_token=raw_refresh_token,
        access_token=access_token,
        access_token_expires_at=access_token_expires_at,
    )


async def refresh(
    db: AsyncSession,
    raw_refresh_token: str | None,
) -> RefreshResult:
    # 1. Refresh Token 쿠키 존재 여부 확인
    if not raw_refresh_token:
        raise unauthorized(
            "REFRESH_TOKEN_MISSING",
            "갱신 토큰이 없습니다. 다시 로그인해주세요.",
        )

    current_hash = security.hash_refresh_token(raw_refresh_token)

    # 2. 해당 Refresh Token 세션을 조회하면서 행 잠금
    #
    # PostgreSQL에서는 동일 Refresh Token으로 동시에 요청이 들어왔을 때
    # 한 요청만 먼저 처리하도록 방어한다.
    result = await db.execute(
        select(LoginSession)
        .where(
            LoginSession.refresh_token_hash == current_hash
        )
        .with_for_update()
    )

    session = result.scalar_one_or_none()

    if session is None:
        raise unauthorized(
            "INVALID_REFRESH_TOKEN",
            "유효하지 않거나 이미 사용된 갱신 토큰입니다.",
        )

    now = utcnow()

    # 3. 이미 종료된 세션 확인
    if session.revoked_at is not None:
        raise unauthorized(
            "SESSION_REVOKED",
            "이미 종료된 로그인 세션입니다.",
        )

    # 4. 절대 만료시간 확인
    #
    # expires_at은 로그인 시 한 번만 결정된다.
    # Refresh 요청으로 절대 연장하지 않는다.
    if session.expires_at <= now:
        session.revoked_at = now
        session.revoke_reason = "최대 세션 사용 시간 초과"

        await db.commit()

        raise unauthorized(
            "SESSION_MAX_LIFETIME_EXCEEDED",
            "로그인 후 최대 사용 시간이 지났습니다. 다시 로그인해주세요.",
        )

    # 5. Idle timeout 확인
    idle_timeout = timedelta(
        minutes=settings.session_idle_timeout_minutes
    )

    if session.last_seen_at + idle_timeout <= now:
        session.revoked_at = now
        session.revoke_reason = "유휴시간 초과"

        await db.commit()

        raise unauthorized(
            "SESSION_IDLE_TIMEOUT",
            "장시간 사용하지 않아 세션이 종료되었습니다. 다시 로그인해주세요.",
        )

    # 6. 세션 소유 직원 조회
    employee = await db.get(
        Employee,
        session.employee_id,
        options=[selectinload(Employee.permissions)],
    )

    if employee is None:
        session.revoked_at = now
        session.revoke_reason = "직원 계정 없음"

        await db.commit()

        raise unauthorized(
            "EMPLOYEE_NOT_FOUND",
            "직원 계정을 찾을 수 없습니다.",
        )

    # 7. 직원 계정 상태 확인
    if employee.status != EmployeeStatus.ACTIVE:
        session.revoked_at = now
        session.revoke_reason = "비활성 직원 계정"

        await db.commit()

        raise unauthorized(
            "ACCOUNT_INACTIVE",
            "비활성화된 직원 계정입니다.",
        )

    # 8. Refresh Token Rotation
    #
    # 기존 Refresh Token 해시를 새 Token 해시로 즉시 교체한다.
    rotated_raw_token = security.generate_raw_refresh_token()

    session.refresh_token_hash = security.hash_refresh_token(
        rotated_raw_token
    )

    # 중요:
    # session.expires_at은 수정하지 않는다.
    # last_seen_at도 Refresh 요청으로 갱신하지 않는다.

    # 9. 새로운 Access Token 발급
    access_token, access_token_expires_at = (
        security.create_access_token(
            employee_code=employee.employee_code,
            session_id=session.id,
            auth_version=employee.auth_version,
            name=employee.name,
            department=employee.department,
        )
    )

    # Refresh Token 교체를 DB에 반영하고 행 잠금 해제
    await db.commit()

    return RefreshResult(
        session=session,
        raw_refresh_token=rotated_raw_token,
        access_token=access_token,
        access_token_expires_at=access_token_expires_at,
    )


async def logout(db: AsyncSession, session_id: uuid.UUID, employee_code: str) -> None:
    session = await db.get(LoginSession, session_id)
    if session is None:
        raise not_found("SESSION_NOT_FOUND", "로그인 세션을 찾을 수 없습니다.")

    employee = await db.get(Employee, session.employee_id)
    if employee is None or employee.employee_code != employee_code:
        raise forbidden("SESSION_OWNER_MISMATCH", "본인의 로그인 세션만 종료할 수 있습니다.")

    if session.revoked_at is None:
        session.revoked_at = utcnow()
        session.revoke_reason = "사용자 로그아웃"
    await db.commit()


async def logout_all(db: AsyncSession, employee_code: str) -> None:
    employee = await _find_employee(db, employee_code)
    now = utcnow()
    await db.execute(
        update(LoginSession)
        .where(LoginSession.employee_id == employee.id, LoginSession.revoked_at.is_(None))
        .values(revoked_at=now, revoke_reason="사용자 전체 로그아웃")
    )
    await db.commit()


async def change_password(db: AsyncSession, employee_code: str, payload: ChangePasswordRequest) -> None:
    employee = await _find_employee(db, employee_code)

    if not security.verify_password(payload.current_password, employee.password_hash):
        raise bad_request("CURRENT_PASSWORD_MISMATCH", "현재 비밀번호가 일치하지 않습니다.")

    if security.verify_password(payload.new_password, employee.password_hash):
        raise bad_request("SAME_PASSWORD", "기존 비밀번호와 다른 비밀번호를 사용해주세요.")

    now = utcnow()
    try:
        employee.password_hash = security.hash_password(payload.new_password)
    except ValueError as exc:
        raise bad_request("INVALID_PASSWORD", str(exc)) from exc
    employee.must_change_password = False
    employee.auth_version += 1
    employee.updated_at = now

    await db.execute(
        update(LoginSession)
        .where(LoginSession.employee_id == employee.id, LoginSession.revoked_at.is_(None))
        .values(revoked_at=now, revoke_reason="비밀번호 변경")
    )
    await db.commit()


async def get_employee(db: AsyncSession, employee_code: str) -> Employee:
    return await _find_employee(db, employee_code)


async def revoke_session(db: AsyncSession, session: LoginSession, reason: str, now: datetime) -> None:
    if session.revoked_at is None:
        session.revoked_at = now
        session.revoke_reason = reason
    await db.commit()
