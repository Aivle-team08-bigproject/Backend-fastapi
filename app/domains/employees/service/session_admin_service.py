import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import not_found
from app.common.time_utils import utcnow
from app.domains.auth.model.session_model import LoginSession
from app.domains.employees.model.audit_log_model import AdminAuditLog
from app.domains.employees.model.employee_model import Employee


async def find_by_employee(db: AsyncSession, employee_code: str) -> list[LoginSession]:
    result = await db.execute(
        select(LoginSession)
        .join(Employee, Employee.id == LoginSession.employee_id)
        .where(Employee.employee_code == employee_code)
        .order_by(LoginSession.created_at.desc())
    )
    return list(result.scalars().all())


async def revoke(db: AsyncSession, session_id: uuid.UUID, operator_code: str) -> None:
    session = await db.get(LoginSession, session_id)
    if session is None:
        raise not_found("SESSION_NOT_FOUND", "로그인 세션을 찾을 수 없습니다.")

    employee = await db.get(Employee, session.employee_id)

    if session.revoked_at is None:
        session.revoked_at = utcnow()
        session.revoke_reason = f"관리자 강제 로그아웃: {operator_code}"
        db.add(
            AdminAuditLog(
                actor_employee_code=operator_code,
                action="SESSION_REVOKED",
                target_employee_code=employee.employee_code if employee else None,
                detail=f"session_id={session_id}",
                created_at=utcnow(),
            )
        )
    await db.commit()
