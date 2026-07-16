import uuid
from dataclasses import dataclass, field
from datetime import timedelta

import jwt
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.errors import forbidden, unauthorized
from app.common.time_utils import utcnow
from app.core import security
from app.core.config import settings
from app.db.session import get_db
from app.domains.auth.model.session_model import LoginSession
from app.domains.auth.service import auth_service
from app.domains.employees.model.employee_model import (
    Employee,
    EmployeeStatus,
    PermissionCode,
)


@dataclass
class CurrentAuth:
    employee: Employee
    session: LoginSession
    must_change_password: bool
    permissions: set[PermissionCode] = field(default_factory=set)


def _extract_bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization")

    if not header or not header.startswith("Bearer "):
        raise unauthorized(
            "UNAUTHORIZED",
            "로그인이 필요하거나 토큰이 만료되었습니다.",
        )

    token = header.removeprefix("Bearer ").strip()

    if not token:
        raise unauthorized(
            "UNAUTHORIZED",
            "Access Token이 비어 있습니다.",
        )

    return token


async def get_current_auth(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> CurrentAuth:
    # 1. Authorization 헤더에서 Access Token 추출
    token = _extract_bearer_token(request)

    # 2. JWT 서명·issuer·audience·만료시간 검증
    try:
        payload = security.decode_access_token(token)
    except jwt.PyJWTError:
        raise unauthorized(
            "UNAUTHORIZED",
            "로그인이 필요하거나 토큰이 만료되었습니다.",
        )

    # 3. JWT의 sid를 UUID로 변환
    try:
        session_id = uuid.UUID(str(payload.get("sid")))
    except (ValueError, TypeError):
        raise unauthorized(
            "UNAUTHORIZED",
            "세션 식별자가 올바르지 않습니다.",
        )

    # 4. DB 로그인 세션 조회
    session = await db.get(LoginSession, session_id)

    if session is None:
        raise unauthorized(
            "UNAUTHORIZED",
            "로그인 세션을 찾을 수 없습니다.",
        )

    now = utcnow()

    # 5. 이미 종료된 세션인지 확인
    if session.revoked_at is not None:
        raise unauthorized(
            "UNAUTHORIZED",
            "이미 종료된 로그인 세션입니다.",
        )

    # 6. 절대 세션 만료시간 확인
    # expires_at은 최초 로그인 시 결정되며 Refresh로 연장하지 않는다.
    if session.expires_at <= now:
        await auth_service.revoke_session(
            db,
            session,
            "최대 세션 사용 시간 초과",
            now,
        )

        raise unauthorized(
            "UNAUTHORIZED",
            "로그인 후 최대 사용 시간이 지났습니다. 다시 로그인해주세요.",
        )

    # 7. Idle timeout 확인
    idle_timeout = timedelta(
        minutes=settings.session_idle_timeout_minutes
    )

    if session.last_seen_at + idle_timeout <= now:
        await auth_service.revoke_session(
            db,
            session,
            "유휴시간 초과",
            now,
        )

        raise unauthorized(
            "UNAUTHORIZED",
            "장시간 사용하지 않아 세션이 종료되었습니다. 다시 로그인해주세요.",
        )

    # 8. 세션과 연결된 직원 조회
    employee = await db.get(
        Employee,
        session.employee_id,
        options=[selectinload(Employee.permissions)],
    )

    if employee is None:
        raise unauthorized(
            "UNAUTHORIZED",
            "직원 계정을 찾을 수 없습니다.",
        )

    # 9. JWT 사용자와 세션 사용자가 같은지 확인
    if employee.employee_code != payload.get("sub"):
        raise unauthorized(
            "UNAUTHORIZED",
            "토큰 사용자와 세션 사용자가 일치하지 않습니다.",
        )

    # 10. 직원 상태 확인
    if employee.status != EmployeeStatus.ACTIVE:
        raise unauthorized(
            "UNAUTHORIZED",
            "비활성화된 직원 계정입니다.",
        )

    # 11. JWT의 인증 버전과 현재 직원 인증 버전 비교
    # 잘못된 ver 값 때문에 500 오류가 발생하지 않도록 예외 처리
    try:
        token_version = int(payload.get("ver"))
    except (TypeError, ValueError):
        raise unauthorized(
            "UNAUTHORIZED",
            "Access Token의 인증 버전이 올바르지 않습니다.",
        )

    if token_version != employee.auth_version:
        raise unauthorized(
            "UNAUTHORIZED",
            "권한 또는 계정 정보가 변경되어 다시 로그인해야 합니다.",
        )

    # 12. 최초 비밀번호 변경 전에는 업무 권한을 부여하지 않음
    if employee.must_change_password:
        permissions: set[PermissionCode] = set()
    else:
        permissions = {
            permission.permission_code
            for permission in employee.permissions
        }

    # 13. 인증 단계에서는 last_seen_at을 갱신하지 않는다.
    # 성공한 업무 API가 끝난 뒤 SessionActivityMiddleware가 사용한다.
    request.state.session_id = session.id
    request.state.session_last_seen_at = session.last_seen_at

    return CurrentAuth(
        employee=employee,
        session=session,
        must_change_password=employee.must_change_password,
        permissions=permissions,
    )


def require_permission(code: PermissionCode):
    async def _dependency(
        auth: CurrentAuth = Depends(get_current_auth),
    ) -> CurrentAuth:
        if code not in auth.permissions:
            raise forbidden(
                "FORBIDDEN",
                "해당 기능을 실행할 권한이 없습니다.",
            )

        return auth

    return _dependency


def require_any_permission(*codes: PermissionCode):
    async def _dependency(
        auth: CurrentAuth = Depends(get_current_auth),
    ) -> CurrentAuth:
        if not any(code in auth.permissions for code in codes):
            raise forbidden(
                "FORBIDDEN",
                "해당 기능을 실행할 권한이 없습니다.",
            )

        return auth

    return _dependency
