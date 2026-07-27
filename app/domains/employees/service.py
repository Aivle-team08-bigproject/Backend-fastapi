import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import security
from app.common.time_utils import utcnow
from app.common.errors import bad_request, conflict, not_found
from app.domains.auth.model.session_model import LoginSession
from app.domains.employees.model import (
    AdminAuditLog,
    Employee,
    EmployeePermission,
    EmployeeRole,
    EmployeeStatus,
    PermissionCode,
)
from app.domains.employees.schema import CreateEmployeeRequest

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

async def _revoke_all_sessions(db: AsyncSession, employee_id: int, reason: str) -> None:
    now = utcnow()
    await db.execute(
        update(LoginSession)
        .where(LoginSession.employee_id == employee_id, LoginSession.revoked_at.is_(None))
        .values(revoked_at=now, revoke_reason=reason)
    )

async def _record_audit_log(
    db: AsyncSession,
    *,
    actor_employee_code: str,
    action: str,
    target_employee_code: str | None = None,
    detail: str | None = None,
) -> None:
    """민감한 관리자 조작을 감사 로그 테이블에 남긴다.

    주의: 이 함수는 db.commit()을 호출하지 않는다. 호출하는 쪽에서 실제 변경사항과
    감사 로그가 같은 트랜잭션으로 묶여서 커밋되도록 한다 (조작은 됐는데 로그만
    안 남는 상황을 피하기 위함).
    """
    db.add(
        AdminAuditLog(
            actor_employee_code=actor_employee_code,
            action=action,
            target_employee_code=target_employee_code,
            detail=detail,
            created_at=utcnow(),
        )
    )

async def _count_other_active_permission_holders(
    db: AsyncSession, permission: PermissionCode, excluding_employee_code: str
) -> int:
    """특정 권한을 가진, ACTIVE 상태의, 대상을 제외한 다른 직원 수를 센다.

    "마지막 관리자 보호"에 사용: 이 권한을 가진 사람이 대상 직원 하나뿐이라면
    그 직원의 권한을 뺏거나 계정을 비활성화하는 조작 자체를 막아야 한다.
    """
    result = await db.execute(
        select(Employee.id)
        .join(EmployeePermission, EmployeePermission.employee_id == Employee.id)
        .where(
            EmployeePermission.permission_code == permission,
            Employee.status == EmployeeStatus.ACTIVE,
            Employee.employee_code != excluding_employee_code,
        )
    )
    return len(result.all())

async def create_employee(
    db: AsyncSession, payload: CreateEmployeeRequest, created_by: str
) -> tuple[Employee, str]:
    existing = await db.execute(
        select(Employee.id).where(Employee.employee_code == payload.employee_code)
    )
    if existing.scalar_one_or_none() is not None:
        raise conflict("EMPLOYEE_CODE_DUPLICATED", "이미 사용 중인 직원 ID입니다.")

    temporary_password = security.generate_temporary_password()
    now = utcnow()

    employee = Employee(
        employee_code=payload.employee_code,
        name=payload.name,
        department=payload.department,
        password_hash=security.hash_password(temporary_password),
        status=EmployeeStatus.ACTIVE,
        must_change_password=True,
        failed_login_count=0,
        auth_version=1,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    employee.permissions = [
        EmployeePermission(permission_code=code) for code in payload.permissions
    ]

    db.add(employee)
    await _record_audit_log(
        db,
        actor_employee_code=created_by,
        action="EMPLOYEE_CREATED",
        target_employee_code=payload.employee_code,
        detail=f"permissions={sorted(p.value for p in payload.permissions)}",
    )
    await db.commit()
    await db.refresh(employee, attribute_names=["permissions"])

    return employee, temporary_password

async def find_all(db: AsyncSession) -> list[Employee]:
    result = await db.execute(
        select(Employee).options(selectinload(Employee.permissions)).order_by(Employee.created_at.desc())
    )
    return list(result.scalars().all())

async def find_one(db: AsyncSession, employee_code: str) -> Employee:
    return await _find_employee(db, employee_code)

async def replace_permissions(
    db: AsyncSession,
    employee_code: str,
    permissions: set[PermissionCode],
    operator_code: str,
) -> Employee:
    employee = await _find_employee(db, employee_code)

    if employee_code == operator_code and PermissionCode.EMPLOYEE_PERMISSION_MANAGE not in permissions:
        raise bad_request(
            "SELF_PERMISSION_REMOVAL_BLOCKED", "본인의 권한 관리 권한은 직접 제거할 수 없습니다."
        )

    # 마지막 관리자 보호: 대상 직원이 EMPLOYEE_PERMISSION_MANAGE를 잃게 되는데,
    # 그 권한을 가진 다른 활성 직원이 아무도 없다면 막는다.
    currently_has_manage = PermissionCode.EMPLOYEE_PERMISSION_MANAGE in {
        p.permission_code for p in employee.permissions
    }
    losing_manage = currently_has_manage and PermissionCode.EMPLOYEE_PERMISSION_MANAGE not in permissions
    if losing_manage:
        other_holders = await _count_other_active_permission_holders(
            db, PermissionCode.EMPLOYEE_PERMISSION_MANAGE, employee_code
        )
        if other_holders == 0:
            raise bad_request(
                "LAST_PERMISSION_MANAGER_BLOCKED",
                "이 직원의 권한 관리 권한을 제거하면 권한을 관리할 수 있는 관리자가 아무도 남지 않습니다.",
            )

    previous = sorted(p.permission_code.value for p in employee.permissions)
    employee.permissions = [EmployeePermission(permission_code=code) for code in permissions]
    employee.auth_version += 1
    employee.updated_at = utcnow()

    await _revoke_all_sessions(db, employee.id, "관리자에 의한 권한 변경")
    await _record_audit_log(
        db,
        actor_employee_code=operator_code,
        action="PERMISSIONS_CHANGED",
        target_employee_code=employee_code,
        detail=f"before={previous} after={sorted(p.value for p in permissions)}",
    )
    await db.commit()
    await db.refresh(employee, attribute_names=["permissions"])

    return employee


ROLE_PERMISSIONS: dict[EmployeeRole, set[PermissionCode]] = {
    EmployeeRole.ADMIN: set(PermissionCode),
    EmployeeRole.MANAGER: {
        PermissionCode.EMPLOYEE_READ,
        PermissionCode.EMPLOYEE_UPDATE,
        PermissionCode.DATA_PRODUCT_READ,
        PermissionCode.QUOTE_READ,
        PermissionCode.QUOTE_PROCESS,
    },
    EmployeeRole.SENIOR: {
        PermissionCode.DATA_PRODUCT_READ,
        PermissionCode.DATA_PRODUCT_WRITE,
        PermissionCode.QUOTE_READ,
    },
    EmployeeRole.GENERAL: {PermissionCode.DATA_PRODUCT_READ, PermissionCode.QUOTE_READ},
}


async def replace_role(
    db: AsyncSession, employee_code: str, role: EmployeeRole, operator_code: str
) -> Employee:
    permissions = ROLE_PERMISSIONS[role]
    return await replace_permissions(db, employee_code, permissions, operator_code)

async def change_status(
    db: AsyncSession, employee_code: str, status: EmployeeStatus, operator_code: str
) -> Employee:
    employee = await _find_employee(db, employee_code)

    if employee_code == operator_code and status != EmployeeStatus.ACTIVE:
        raise bad_request("SELF_DISABLE_BLOCKED", "본인 계정은 직접 잠그거나 비활성화할 수 없습니다.")

    # 마지막 관리자 보호: ACTIVE가 아닌 상태로 바뀌면 이 직원은 사실상 아무 권한도
    # 행사할 수 없게 된다. EMPLOYEE_PERMISSION_MANAGE를 가진 마지막 활성 직원이라면 막는다.
    has_manage = PermissionCode.EMPLOYEE_PERMISSION_MANAGE in {p.permission_code for p in employee.permissions}
    if status != EmployeeStatus.ACTIVE and has_manage:
        other_holders = await _count_other_active_permission_holders(
            db, PermissionCode.EMPLOYEE_PERMISSION_MANAGE, employee_code
        )
        if other_holders == 0:
            raise bad_request(
                "LAST_PERMISSION_MANAGER_BLOCKED",
                "이 직원을 비활성화하면 권한을 관리할 수 있는 관리자가 아무도 남지 않습니다.",
            )

    previous_status = employee.status.value
    employee.status = status
    employee.auth_version += 1
    employee.updated_at = utcnow()

    await _revoke_all_sessions(db, employee.id, f"직원 상태 변경: {status}")
    await _record_audit_log(
        db,
        actor_employee_code=operator_code,
        action="STATUS_CHANGED",
        target_employee_code=employee_code,
        detail=f"before={previous_status} after={status.value}",
    )
    await db.commit()

    return employee

async def reset_password(db: AsyncSession, employee_code: str, operator_code: str) -> tuple[Employee, str]:
    employee = await _find_employee(db, employee_code)
    temporary_password = security.generate_temporary_password()

    employee.password_hash = security.hash_password(temporary_password)
    employee.must_change_password = True
    employee.auth_version += 1
    employee.updated_at = utcnow()

    await _revoke_all_sessions(db, employee.id, "관리자에 의한 비밀번호 초기화")
    await _record_audit_log(
        db,
        actor_employee_code=operator_code,
        action="PASSWORD_RESET",
        target_employee_code=employee_code,
    )
    await db.commit()

    return employee, temporary_password

async def list_audit_logs(db: AsyncSession, limit: int = 200) -> list[AdminAuditLog]:
    result = await db.execute(
        select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def find_sessions_by_employee(db: AsyncSession, employee_code: str) -> list[LoginSession]:
    result = await db.execute(
        select(LoginSession)
        .join(Employee, Employee.id == LoginSession.employee_id)
        .where(Employee.employee_code == employee_code)
        .order_by(LoginSession.created_at.desc())
    )
    return list(result.scalars().all())


async def revoke_session(db: AsyncSession, session_id: uuid.UUID, operator_code: str) -> None:
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
