import re
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.config import settings
from app.domains.employees.model import PositionType

_PASSWORD_PATTERN = re.compile(r"^(?=.*[A-Z])(?=.*[a-z])(?=.*\d)(?=.*[^A-Za-z0-9]).+$")
_PHONE_PATTERN = re.compile(r"01[016789]\d{7,8}")


def validate_password_complexity(value: str) -> str:
    if not _PASSWORD_PATTERN.match(value):
        raise ValueError("대문자, 소문자, 숫자, 특수문자를 각각 하나 이상 포함해야 합니다.")
    return value


def validate_password_byte_length(value: str) -> str:
    # bcrypt는 72바이트를 넘는 입력을 처리하지 못한다. max_length는 "글자 수" 기준이라
    # 한글처럼 UTF-8에서 1글자가 3바이트인 문자가 섞이면 글자 수 제한 안에서도 72바이트를 넘을 수 있다.
    # 여기서 미리 막지 않으면 core.security.hash_password가 처리되지 않은 ValueError를
    # 던져서 500 에러로 샌다.
    byte_length = len(value.encode("utf-8"))
    if byte_length > 72:
        raise ValueError(
            f"비밀번호는 최대 72바이트(UTF-8 기준)를 초과할 수 없습니다. (현재 {byte_length}바이트)"
        )
    return value


def validate_company_email(value: str) -> str:
    """이메일을 정규화(소문자/공백제거)하고 허용된 회사 도메인인지 확인한다.

    이 서비스는 회사 직원 전용이므로, 회원가입뿐 아니라 관리자가 직원 계정을 만들 때도
    동일하게 적용한다. 허용 도메인 목록은 ALLOWED_EMAIL_DOMAINS 환경변수로 관리한다
    (코드 변경 없이 운영 중에도 조정 가능).
    """
    normalized = value.strip().lower()
    domain = normalized.rsplit("@", 1)[-1]
    if domain not in settings.allowed_email_domain_list():
        raise ValueError("허용되지 않은 이메일 도메인입니다. 회사 이메일로 가입해주세요.")
    return normalized


def validate_and_normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if not _PHONE_PATTERN.fullmatch(digits):
        raise ValueError("올바른 휴대폰 번호 형식이 아닙니다.")
    return digits


class LoginRequest(BaseModel):
    email: EmailStr = Field(..., description="가입 시 등록한 회사 이메일")
    password: str = Field(..., min_length=1)
    remember_me: bool = False

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=12, max_length=64)

    @field_validator("new_password")
    @classmethod
    def _validate_complexity(cls, value: str) -> str:
        return validate_password_complexity(value)

    @field_validator("new_password")
    @classmethod
    def _validate_byte_length(cls, value: str) -> str:
        return validate_password_byte_length(value)


class SignupRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=50)
    email: EmailStr = Field(..., description="회사 이메일 (로그인 ID로 사용)")
    phone: str = Field(..., min_length=10, max_length=20)
    department_id: int = Field(..., description="GET /api/public/departments 목록에서 선택")
    position: PositionType
    password: str = Field(..., min_length=12, max_length=64)
    terms_agreed: bool = Field(..., description="서비스 이용약관 동의")
    privacy_agreed: bool = Field(..., description="개인정보 수집 및 이용 동의")

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: EmailStr) -> str:
        return validate_company_email(str(value))

    @field_validator("phone")
    @classmethod
    def _validate_phone(cls, value: str) -> str:
        return validate_and_normalize_phone(value)

    @field_validator("password")
    @classmethod
    def _validate_password_complexity(cls, value: str) -> str:
        return validate_password_complexity(value)

    @field_validator("password")
    @classmethod
    def _validate_password_byte_length(cls, value: str) -> str:
        return validate_password_byte_length(value)

    @field_validator("terms_agreed")
    @classmethod
    def _require_terms_agreed(cls, value: bool) -> bool:
        if not value:
            raise ValueError("서비스 이용약관 동의가 필요합니다.")
        return value

    @field_validator("privacy_agreed")
    @classmethod
    def _require_privacy_agreed(cls, value: bool) -> bool:
        if not value:
            raise ValueError("개인정보 수집 및 이용 동의가 필요합니다.")
        return value


class SignupResponse(BaseModel):
    employee_code: str
    email: str
    status: str
    message: str = "회원가입 신청이 완료되었습니다. 관리자 승인 후 로그인할 수 있습니다."


class EmployeeSummary(BaseModel):
    employee_code: str
    name: str
    email: str
    department_id: int | None
    department_name: str | None
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
