import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum as SAEnum, ForeignKey, Integer, String, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AdminAuditLog(Base):
    """관리자 조작을 기록하는 감사 로그."""

    __tablename__ = "admin_audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_employee_code: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    target_employee_code: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class EmployeeStatus(str, enum.Enum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    LOCKED = "LOCKED"
    DISABLED = "DISABLED"


class EmployeeRole(str, enum.Enum):
    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    SENIOR = "SENIOR"
    GENERAL = "GENERAL"


class PositionType(str, enum.Enum):
    """직급. role(시스템 권한)과는 별개의 조직 정보이며 자동으로 권한과 연결되지 않는다."""

    STAFF = "STAFF"
    ASSISTANT_MANAGER = "ASSISTANT_MANAGER"
    MANAGER = "MANAGER"
    DEPUTY_GENERAL_MANAGER = "DEPUTY_GENERAL_MANAGER"
    GENERAL_MANAGER = "GENERAL_MANAGER"


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


class Department(Base):
    """부서. 자유 문자열로 두면 오탈자로 같은 부서가 여러 형태로 저장될 수 있어 별도 테이블로 관리한다."""

    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Role(Base):
    """역할(시스템 권한 묶음)의 정본. EmployeeRole enum과 코드값을 동일하게 유지한다."""

    __tablename__ = "roles"

    role_code: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Permission(Base):
    """권한 코드의 정본. PermissionCode enum과 코드값을 동일하게 유지한다."""

    __tablename__ = "permissions"

    permission_code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RolePermission(Base):
    """역할별 기본 권한 템플릿. 승인/역할변경 시 이 조합이 employee_permissions로 복사된다."""

    __tablename__ = "role_permissions"

    role_code: Mapped[str] = mapped_column(String(30), ForeignKey("service.roles.role_code"), primary_key=True)
    permission_code: Mapped[str] = mapped_column(
        String(40), ForeignKey("service.permissions.permission_code"), primary_key=True
    )


class ConsentLog(Base):
    """약관/개인정보 동의 이력. employees의 동의 컬럼은 "현재 상태"만 담고, 여기엔 매 동의 시점의
    스냅샷이 계속 쌓인다 — 약관이 개정돼 재동의를 받아도 과거 동의 기록이 사라지지 않는다."""

    __tablename__ = "consent_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("service.employees.id"), nullable=False, index=True)
    consent_type: Mapped[str] = mapped_column(String(30), nullable=False)  # "TERMS" | "PRIVACY"
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    agreed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    department_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("service.departments.id"), nullable=True, index=True
    )
    position: Mapped[PositionType | None] = mapped_column(
        SAEnum(PositionType, native_enum=False, length=30), nullable=True
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[EmployeeStatus] = mapped_column(
        SAEnum(EmployeeStatus, native_enum=False, length=20), nullable=False, default=EmployeeStatus.ACTIVE
    )
    role_code: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("service.roles.role_code"), nullable=True
    )
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auth_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 회원가입 시 동의한 약관 이력. Boolean 하나만 두면 나중에 약관이 바뀌었을 때
    # 어떤 버전에 동의했는지 알 수 없으므로 시각과 버전을 같이 남긴다.
    terms_agreed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terms_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    privacy_agreed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    privacy_version: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # 관리자 승인/거절 이력
    approved_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_by: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    permissions: Mapped[list["EmployeePermission"]] = relationship(
        back_populates="employee",
        cascade="all, delete-orphan",
        lazy="selectin",  # 직원을 읽을 때 권한도 항상 같이 로드 (N+1 방지)
    )
    department: Mapped["Department | None"] = relationship(lazy="selectin")


class EmployeePermission(Base):
    __tablename__ = "employee_permissions"

    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("service.employees.id"), primary_key=True
    )
    permission_code: Mapped[PermissionCode] = mapped_column(
        SAEnum(PermissionCode, native_enum=False, length=40), primary_key=True
    )

    employee: Mapped["Employee"] = relationship(back_populates="permissions")
