import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

_PASSWORD_PATTERN = re.compile(r"^(?=.*[A-Z])(?=.*[a-z])(?=.*\d)(?=.*[^A-Za-z0-9]).+$")


class LoginRequest(BaseModel):
    employee_code: str = Field(..., min_length=1, description="직원 ID")
    password: str = Field(..., min_length=1)
    remember_me: bool = False


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=12, max_length=64)

    @field_validator("new_password")
    @classmethod
    def _validate_complexity(cls, value: str) -> str:
        if not _PASSWORD_PATTERN.match(value):
            raise ValueError("대문자, 소문자, 숫자, 특수문자를 각각 하나 이상 포함해야 합니다.")
        return value

    @field_validator("new_password")
    @classmethod
    def _validate_byte_length(cls, value: str) -> str:
        # bcrypt는 72바이트를 넘는 입력을 처리하지 못한다. max_length=64는 "글자 수" 기준이라
        # 한글처럼 UTF-8에서 1글자가 3바이트인 문자가 섞이면 64자여도 72바이트를 넘을 수 있다.
        # 여기서 미리 막지 않으면 core.security.hash_password가 처리되지 않은 ValueError를
        # 던져서 500 에러로 샌다.
        byte_length = len(value.encode("utf-8"))
        if byte_length > 72:
            raise ValueError(
                f"비밀번호는 최대 72바이트(UTF-8 기준)를 초과할 수 없습니다. (현재 {byte_length}바이트)"
            )
        return value


class EmployeeSummary(BaseModel):
    employee_code: str
    name: str
    department: str
    status: str
    must_change_password: bool
    permissions: list[str]


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in_seconds: int
    expires_at: datetime
    employee: EmployeeSummary


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in_seconds: int
    expires_at: datetime
