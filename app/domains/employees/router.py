import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.masking import mask_phone
from app.common.security_deps import (
    CurrentAuth,
    require_admin_or_permission,
    require_any_permission,
    require_permission,
)
from app.db.session import get_db
from app.common.time_utils import utcnow
from app.domains.auth.model.session_model import LoginSession
from app.domains.employees.model import (
    AdminAuditLog,
    PERMISSION_DESCRIPTIONS,
    Employee,
    EmployeeRole,
    PermissionCode,
)
from app.domains.employees.schema import (
    ApproveSignupRequest,
    AuditLogResponse,
    CreateEmployeeRequest,
    CreateEmployeeResponse,
    DepartmentResponse,
    EmployeeResponse,
    PermissionCatalogItem,
    RejectSignupRequest,
    ResetPasswordResponse,
    SessionResponse,
    UpdatePermissionsRequest,
    UpdateRoleRequest,
    UpdateStatusRequest,
)
from app.domains.employees import service as employee_service

router = APIRouter(prefix="/api/admin/employees", tags=["employees-admin"])
session_router = APIRouter(prefix="/api/admin", tags=["session-admin"])
public_router = APIRouter(prefix="/api/public", tags=["public"])


def _to_response(employee: Employee) -> EmployeeResponse:
    return EmployeeResponse(
        employee_code=employee.employee_code,
        name=employee.name,
        email=employee.email,
        phone_masked=mask_phone(employee.phone),
        department_id=employee.department_id,
        department_name=employee.department.name if employee.department else None,
        position=employee.position,
        role=EmployeeRole(employee.role_code) if employee.role_code else None,
        status=employee.status,
        must_change_password=employee.must_change_password,
        permissions=[p.permission_code for p in employee.permissions],
        approved_by=employee.approved_by,
        approved_at=employee.approved_at,
        rejected_reason=employee.rejected_reason,
        last_login_at=employee.last_login_at,
        created_by=employee.created_by,
        created_at=employee.created_at,
        updated_at=employee.updated_at,
    )


@public_router.get("/departments", response_model=list[DepartmentResponse])
async def list_departments(db: AsyncSession = Depends(get_db)) -> list[DepartmentResponse]:
    departments = await employee_service.list_departments(db)
    return [DepartmentResponse(id=d.id, name=d.name, code=d.code) for d in departments]


def _audit_log_to_response(log: AdminAuditLog) -> AuditLogResponse:
    return AuditLogResponse(
        id=log.id,
        actor_employee_code=log.actor_employee_code,
        action=log.action,
        target_employee_code=log.target_employee_code,
        detail=log.detail,
        created_at=log.created_at,
    )


# 주의: 정적 경로(/permissions/catalog, /audit-logs)를 동적 경로(/{employee_code})보다 먼저 등록한다.
# 세그먼트 개수가 달라서(둘 다 한 단계 vs 두 단계) 실제로는 충돌하지 않지만,
# 가독성과 향후 실수 방지를 위해 순서를 지킨다.

@router.get("/permissions/catalog", response_model=list[PermissionCatalogItem])
async def permission_catalog(
    auth: CurrentAuth = Depends(
        require_any_permission(PermissionCode.EMPLOYEE_CREATE, PermissionCode.EMPLOYEE_PERMISSION_MANAGE)
    ),
) -> list[PermissionCatalogItem]:
    return [
        PermissionCatalogItem(code=code.value, description=description)
        for code, description in PERMISSION_DESCRIPTIONS.items()
    ]


@router.get("/audit-logs", response_model=list[AuditLogResponse])
async def audit_logs(
    auth: CurrentAuth = Depends(require_permission(PermissionCode.AUDIT_READ)),
    db: AsyncSession = Depends(get_db),
) -> list[AuditLogResponse]:
    logs = await employee_service.list_audit_logs(db)
    return [_audit_log_to_response(log) for log in logs]


@router.get("", response_model=list[EmployeeResponse])
async def list_employees(
    auth: CurrentAuth = Depends(require_permission(PermissionCode.EMPLOYEE_READ)),
    db: AsyncSession = Depends(get_db),
) -> list[EmployeeResponse]:
    employees = await employee_service.find_all(db)
    return [_to_response(e) for e in employees]


@router.get("/signup-requests", response_model=list[EmployeeResponse])
async def list_signup_requests(
    auth: CurrentAuth = Depends(
        require_any_permission(PermissionCode.EMPLOYEE_CREATE, PermissionCode.EMPLOYEE_PERMISSION_MANAGE)
    ),
    db: AsyncSession = Depends(get_db),
) -> list[EmployeeResponse]:
    employees = await employee_service.list_pending_signups(db)
    return [_to_response(e) for e in employees]


@router.post("/signup-requests/{employee_code}/approve", response_model=EmployeeResponse)
async def approve_signup_request(
    employee_code: str,
    payload: ApproveSignupRequest,
    auth: CurrentAuth = Depends(require_admin_or_permission(PermissionCode.EMPLOYEE_PERMISSION_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> EmployeeResponse:
    employee = await employee_service.approve_signup(
        db, employee_code, payload, auth.employee.employee_code
    )
    return _to_response(employee)


@router.post("/signup-requests/{employee_code}/reject", response_model=EmployeeResponse)
async def reject_signup_request(
    employee_code: str,
    payload: RejectSignupRequest,
    auth: CurrentAuth = Depends(require_permission(PermissionCode.EMPLOYEE_UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> EmployeeResponse:
    employee = await employee_service.reject_signup(
        db, employee_code, payload.reason, auth.employee.employee_code
    )
    return _to_response(employee)


@router.get("/{employee_code}", response_model=EmployeeResponse)
async def get_employee(
    employee_code: str,
    auth: CurrentAuth = Depends(require_permission(PermissionCode.EMPLOYEE_READ)),
    db: AsyncSession = Depends(get_db),
) -> EmployeeResponse:
    employee = await employee_service.find_one(db, employee_code)
    return _to_response(employee)


@router.post("", response_model=CreateEmployeeResponse)
async def create_employee(
    payload: CreateEmployeeRequest,
    auth: CurrentAuth = Depends(require_permission(PermissionCode.EMPLOYEE_CREATE)),
    db: AsyncSession = Depends(get_db),
) -> CreateEmployeeResponse:
    employee, temporary_password = await employee_service.create_employee(
        db, payload, auth.employee.employee_code
    )
    return CreateEmployeeResponse(employee=_to_response(employee), temporary_password=temporary_password)


@router.put("/{employee_code}/permissions", response_model=EmployeeResponse)
async def replace_permissions(
    employee_code: str,
    payload: UpdatePermissionsRequest,
    auth: CurrentAuth = Depends(require_admin_or_permission(PermissionCode.EMPLOYEE_PERMISSION_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> EmployeeResponse:
    employee = await employee_service.replace_permissions(
        db, employee_code, payload.permissions, auth.employee.employee_code
    )
    return _to_response(employee)


@router.patch("/{employee_code}/role", response_model=EmployeeResponse)
async def replace_role(
    employee_code: str,
    payload: UpdateRoleRequest,
    auth: CurrentAuth = Depends(require_admin_or_permission(PermissionCode.EMPLOYEE_PERMISSION_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> EmployeeResponse:
    employee = await employee_service.replace_role(
        db, employee_code, payload.role, auth.employee.employee_code
    )
    return _to_response(employee)


@router.patch("/{employee_code}/status", response_model=EmployeeResponse)
async def change_status(
    employee_code: str,
    payload: UpdateStatusRequest,
    auth: CurrentAuth = Depends(require_admin_or_permission(PermissionCode.EMPLOYEE_UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> EmployeeResponse:
    employee = await employee_service.change_status(
        db, employee_code, payload.status, auth.employee.employee_code
    )
    return _to_response(employee)


@router.post("/{employee_code}/reset-password", response_model=ResetPasswordResponse)
async def reset_password(
    employee_code: str,
    auth: CurrentAuth = Depends(require_admin_or_permission(PermissionCode.EMPLOYEE_UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> ResetPasswordResponse:
    employee, temporary_password = await employee_service.reset_password(
        db, employee_code, auth.employee.employee_code
    )
    return ResetPasswordResponse(employee_code=employee.employee_code, temporary_password=temporary_password)


def _session_to_response(session: LoginSession) -> SessionResponse:
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


@session_router.get("/employees/{employee_code}/sessions", response_model=list[SessionResponse])
async def list_sessions(
    employee_code: str,
    auth: CurrentAuth = Depends(require_permission(PermissionCode.EMPLOYEE_SESSION_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> list[SessionResponse]:
    sessions = await employee_service.find_sessions_by_employee(db, employee_code)
    return [_session_to_response(session) for session in sessions]


@session_router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: uuid.UUID,
    auth: CurrentAuth = Depends(require_permission(PermissionCode.EMPLOYEE_SESSION_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    await employee_service.revoke_session(db, session_id, auth.employee.employee_code)
