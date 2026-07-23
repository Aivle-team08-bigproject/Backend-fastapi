import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, String, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class EmployeeStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    LOCKED = "LOCKED"
    DISABLED = "DISABLED"


class EmployeeRole(str, enum.Enum):
    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    SENIOR = "SENIOR"
    GENERAL = "GENERAL"


class PermissionCode(str, enum.Enum):
    EMPLOYEE_READ = "EMPLOYEE_READ"
    EMPLOYEE_CREATE = "EMPLOYEE_CREATE"
    EMPLOYEE_UPDATE = "EMPLOYEE_UPDATE"
    EMPLOYEE_PERMISSION_MANAGE = "EMPLOYEE_PERMISSION_MANAGE"
    EMPLOYEE_SESSION_MANAGE = "EMPLOYEE_SESSION_MANAGE"
    AUDIT_READ = "AUDIT_READ"
    DATA_PRODUCT_READ = "DATA_PRODUCT_READ"
    DATA_PRODUCT_WRITE = "DATA_PRODUCT_WRITE"
    QUOTE_READ = "QUOTE_READ"
    QUOTE_PROCESS = "QUOTE_PROCESS"
    CONTRACT_MANAGE = "CONTRACT_MANAGE"


PERMISSION_DESCRIPTIONS: dict[PermissionCode, str] = {
    PermissionCode.EMPLOYEE_READ: "직원 조회",
    PermissionCode.EMPLOYEE_CREATE: "직원 계정 생성",
    PermissionCode.EMPLOYEE_UPDATE: "직원 정보 및 상태 변경",
    PermissionCode.EMPLOYEE_PERMISSION_MANAGE: "직원 권한 변경",
    PermissionCode.EMPLOYEE_SESSION_MANAGE: "직원 세션 조회 및 강제 로그아웃",
    PermissionCode.AUDIT_READ: "감사 로그 조회",
    PermissionCode.DATA_PRODUCT_READ: "데이터 상품 조회",
    PermissionCode.DATA_PRODUCT_WRITE: "데이터 상품 등록 및 수정",
    PermissionCode.QUOTE_READ: "견적 요청 조회",
    PermissionCode.QUOTE_PROCESS: "견적 요청 처리",
    PermissionCode.CONTRACT_MANAGE: "계약 관리",
}


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    department: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[EmployeeStatus] = mapped_column(
        SAEnum(EmployeeStatus, native_enum=False, length=20), nullable=False, default=EmployeeStatus.ACTIVE
    )
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    auth_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)

    permissions: Mapped[list["EmployeePermission"]] = relationship(
        back_populates="employee",
        cascade="all, delete-orphan",
        lazy="selectin",  # 직원을 읽을 때 권한도 항상 같이 로드 (N+1 방지)
    )


class EmployeePermission(Base):
    __tablename__ = "employee_permissions"

    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), primary_key=True)
    permission_code: Mapped[PermissionCode] = mapped_column(
        SAEnum(PermissionCode, native_enum=False, length=80), primary_key=True
    )

    employee: Mapped["Employee"] = relationship(back_populates="permissions")
