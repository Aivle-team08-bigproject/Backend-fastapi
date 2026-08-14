import re
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.domains.auth.schema.auth_schema import validate_company_email
from app.domains.employees.model import EmployeeRole, EmployeeStatus, PermissionCode, PositionType

_EMPLOYEE_CODE_PATTERN = re.compile(r"^[A-Z0-9-]{5,40}$")


class CreateEmployeeRequest(BaseModel):
    employee_code: str = Field(..., description="영문 대문자/숫자/하이픈, 5~40자")
    name: str = Field(..., min_length=1, max_length=80)
    email: EmailStr = Field(..., description="회사 이메일 (로그인 ID로 사용)")
    department_id: int = Field(..., description="GET /api/public/departments 목록에서 선택")
    permissions: set[PermissionCode]

    @field_validator("employee_code")
    @classmethod
    def _validate_code(cls, value: str) -> str:
        if not _EMPLOYEE_CODE_PATTERN.match(value):
            raise ValueError("직원 ID는 영문 대문자, 숫자, 하이픈만 사용할 수 있습니다.")
        return value

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: EmailStr) -> str:
        return validate_company_email(str(value))


class UpdatePermissionsRequest(BaseModel):
    permissions: set[PermissionCode]


class UpdateRoleRequest(BaseModel):
    role: EmployeeRole


class UpdateStatusRequest(BaseModel):
    status: EmployeeStatus


class ApproveSignupRequest(BaseModel):
    role: EmployeeRole
    department_id: int | None = None
    position: PositionType | None = None


class RejectSignupRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)
    # 반려 사유에 개인정보가 섞였을 때의 확인 플래그. 파이프라인 쪽과 같은 규약이다.
    confirm_pii: bool = False


class DepartmentResponse(BaseModel):
    id: int
    name: str
    code: str


class EmployeeResponse(BaseModel):
    employee_code: str
    name: str
    # 과도기: NeonDB에 이메일 없이 만들어진 기존 계정 6건이 있어 NULL 허용.
    # 값이 다 채워지고 NOT NULL 전환되면 다시 str로 좁힐 것.
    email: str | None
    phone_masked: str | None
    department_id: int | None
    department_name: str | None
    position: PositionType | None
    role: EmployeeRole | None
    status: EmployeeStatus
    must_change_password: bool
    permissions: list[PermissionCode]
    approved_by: str | None
    approved_at: datetime | None
    rejected_reason: str | None
    last_login_at: datetime | None
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
