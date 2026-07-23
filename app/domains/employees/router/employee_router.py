from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.security_deps import CurrentAuth, require_any_permission, require_permission
from app.db.session import get_db
from app.domains.employees.model.employee_model import (
    PERMISSION_DESCRIPTIONS,
    Employee,
    PermissionCode,
)
from app.domains.employees.model.audit_log_model import AdminAuditLog
from app.domains.employees.schema.employee_schema import (
    AuditLogResponse,
    CreateEmployeeRequest,
    CreateEmployeeResponse,
    EmployeeResponse,
    PermissionCatalogItem,
    ResetPasswordResponse,
    UpdatePermissionsRequest,
    UpdateRoleRequest,
    UpdateStatusRequest,
)
from app.domains.employees.service import employee_service

router = APIRouter(prefix="/api/admin/employees", tags=["employees-admin"])


def _to_response(employee: Employee) -> EmployeeResponse:
    return EmployeeResponse(
        employee_code=employee.employee_code,
        name=employee.name,
        department=employee.department,
        status=employee.status,
        must_change_password=employee.must_change_password,
        permissions=[p.permission_code for p in employee.permissions],
        created_by=employee.created_by,
        created_at=employee.created_at,
        updated_at=employee.updated_at,
    )


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
    auth: CurrentAuth = Depends(require_permission(PermissionCode.EMPLOYEE_PERMISSION_MANAGE)),
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
    auth: CurrentAuth = Depends(require_permission(PermissionCode.EMPLOYEE_PERMISSION_MANAGE)),
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
    auth: CurrentAuth = Depends(require_permission(PermissionCode.EMPLOYEE_UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> EmployeeResponse:
    employee = await employee_service.change_status(
        db, employee_code, payload.status, auth.employee.employee_code
    )
    return _to_response(employee)


@router.post("/{employee_code}/reset-password", response_model=ResetPasswordResponse)
async def reset_password(
    employee_code: str,
    auth: CurrentAuth = Depends(require_permission(PermissionCode.EMPLOYEE_UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> ResetPasswordResponse:
    employee, temporary_password = await employee_service.reset_password(
        db, employee_code, auth.employee.employee_code
    )
    return ResetPasswordResponse(employee_code=employee.employee_code, temporary_password=temporary_password)
