import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.domains.employees.model.employee_model import EmployeeStatus, PermissionCode

_EMPLOYEE_CODE_PATTERN = re.compile(r"^[A-Z0-9-]{5,40}$")


class CreateEmployeeRequest(BaseModel):
    employee_code: str = Field(..., description="영문 대문자/숫자/하이픈, 5~40자")
    name: str = Field(..., min_length=1, max_length=80)
    department: str = Field(..., min_length=1, max_length=100)
    permissions: set[PermissionCode]

    @field_validator("employee_code")
    @classmethod
    def _validate_code(cls, value: str) -> str:
        if not _EMPLOYEE_CODE_PATTERN.match(value):
            raise ValueError("직원 ID는 영문 대문자, 숫자, 하이픈만 사용할 수 있습니다.")
        return value


class UpdatePermissionsRequest(BaseModel):
    permissions: set[PermissionCode]


class UpdateStatusRequest(BaseModel):
    status: EmployeeStatus


class EmployeeResponse(BaseModel):
    employee_code: str
    name: str
    department: str
    status: EmployeeStatus
    must_change_password: bool
    permissions: list[PermissionCode]
    created_by: str
    created_at: datetime
    updated_at: datetime


class CreateEmployeeResponse(BaseModel):
    employee: EmployeeResponse
    temporary_password: str
    notice: str = "임시 비밀번호는 이 응답에서만 확인할 수 있습니다. 안전한 채널로 직원에게 전달하세요."


class ResetPasswordResponse(BaseModel):
    employee_code: str
    temporary_password: str
    notice: str = "임시 비밀번호는 이 응답에서만 확인할 수 있습니다."


class PermissionCatalogItem(BaseModel):
    code: str
    description: str


class SessionResponse(BaseModel):
    session_id: str
    active: bool
    remember_me: bool
    ip_address: str | None
    user_agent: str | None
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    revoke_reason: str | None


class AuditLogResponse(BaseModel):
    id: int
    actor_employee_code: str
    action: str
    target_employee_code: str | None
    detail: str | None
    created_at: datetime
