import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.security_deps import CurrentAuth, require_permission
from app.common.time_utils import utcnow
from app.db.session import get_db
from app.domains.auth.model.session_model import LoginSession
from app.domains.employees.model.employee_model import PermissionCode
from app.domains.employees.schema.employee_schema import SessionResponse
from app.domains.employees.service import session_admin_service

router = APIRouter(prefix="/api/admin", tags=["session-admin"])


def _to_response(session: LoginSession) -> SessionResponse:
    return SessionResponse(
        session_id=str(session.id),
        active=session.is_active(utcnow()),
        remember_me=session.remember_me,
        ip_address=session.ip_address,
        user_agent=session.user_agent,
        created_at=session.created_at,
        last_seen_at=session.last_seen_at,
        expires_at=session.expires_at,
        revoked_at=session.revoked_at,
        revoke_reason=session.revoke_reason,
    )

#직원 세션 조회 및 강제 로그아웃 관련 API
@router.get("/employees/{employee_code}/sessions", response_model=list[SessionResponse])
async def list_sessions(
    employee_code: str,
    auth: CurrentAuth = Depends(require_permission(PermissionCode.EMPLOYEE_SESSION_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> list[SessionResponse]:
    sessions = await session_admin_service.find_by_employee(db, employee_code)
    return [_to_response(s) for s in sessions]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: uuid.UUID,
    auth: CurrentAuth = Depends(require_permission(PermissionCode.EMPLOYEE_SESSION_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    await session_admin_service.revoke(db, session_id, auth.employee.employee_code)
