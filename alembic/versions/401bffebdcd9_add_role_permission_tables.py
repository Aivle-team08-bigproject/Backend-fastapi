"""normalize role/permission mapping into tables, add consent_logs, last_login_at

Revision ID: 401bffebdcd9
Revises: d62d1592c2e9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "401bffebdcd9"
down_revision: str | None = "d62d1592c2e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# EmployeeRole enum과 동일한 값. 화면 표기 라벨은 기존 대시보드 코드가 이미 쓰던 것과 맞췄다.
_ROLES = [
    ("ADMIN", "관리자", 1),
    ("MANAGER", "책임자", 2),
    ("SENIOR", "선임", 3),
    ("GENERAL", "일반", 4),
]

# PermissionCode enum + PERMISSION_DESCRIPTIONS와 동일한 값.
_PERMISSIONS = [
    ("EMPLOYEE_READ", "직원 조회"),
    ("EMPLOYEE_CREATE", "직원 계정 생성"),
    ("EMPLOYEE_UPDATE", "직원 정보 및 상태 변경"),
    ("EMPLOYEE_PERMISSION_MANAGE", "직원 권한 변경"),
    ("EMPLOYEE_SESSION_MANAGE", "직원 세션 조회 및 강제 로그아웃"),
    ("AUDIT_READ", "감사 로그 조회"),
    ("DATA_PRODUCT_READ", "데이터 상품 조회"),
    ("DATA_PRODUCT_WRITE", "데이터 상품 등록 및 수정"),
    ("QUOTE_READ", "견적 요청 조회"),
    ("QUOTE_PROCESS", "견적 요청 처리"),
    ("CONTRACT_MANAGE", "계약 관리"),
]

# 기존 코드에 하드코딩돼 있던 ROLE_PERMISSIONS dict와 동일한 값 (기존 테스트가 이 값을 전제로 함).
_ROLE_PERMISSIONS = {
    "ADMIN": [code for code, _ in _PERMISSIONS],
    "MANAGER": ["EMPLOYEE_READ", "EMPLOYEE_UPDATE", "DATA_PRODUCT_READ", "QUOTE_READ", "QUOTE_PROCESS"],
    "SENIOR": ["DATA_PRODUCT_READ", "DATA_PRODUCT_WRITE", "QUOTE_READ"],
    "GENERAL": ["DATA_PRODUCT_READ", "QUOTE_READ"],
}


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("role_code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("role_code"),
        schema="service",
    )
    op.create_table(
        "permissions",
        sa.Column("permission_code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("permission_code"),
        schema="service",
    )
    op.create_table(
        "role_permissions",
        sa.Column("role_code", sa.String(length=30), nullable=False),
        sa.Column("permission_code", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["role_code"], ["service.roles.role_code"]),
        sa.ForeignKeyConstraint(["permission_code"], ["service.permissions.permission_code"]),
        sa.PrimaryKeyConstraint("role_code", "permission_code"),
        schema="service",
    )
    op.create_table(
        "consent_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("consent_type", sa.String(length=30), nullable=False),
        sa.Column("version", sa.String(length=20), nullable=False),
        sa.Column("agreed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["employee_id"], ["service.employees.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="service",
    )
    op.create_index(
        op.f("ix_service_consent_logs_employee_id"), "consent_logs", ["employee_id"], unique=False, schema="service"
    )

    op.add_column(
        "employees",
        sa.Column("role_code", sa.String(length=30), sa.ForeignKey("service.roles.role_code"), nullable=True),
        schema="service",
    )
    op.add_column("employees", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True), schema="service")

    connection = op.get_bind()

    insert_role = sa.text(
        "INSERT INTO service.roles (role_code, name, display_order) VALUES (:code, :name, :order)"
    )
    for code, name, order in _ROLES:
        connection.execute(insert_role.bindparams(code=code, name=name, order=order))

    insert_permission = sa.text(
        "INSERT INTO service.permissions (permission_code, name, is_default, display_order) "
        "VALUES (:code, :name, false, :order)"
    )
    for order, (code, name) in enumerate(_PERMISSIONS):
        connection.execute(insert_permission.bindparams(code=code, name=name, order=order))

    insert_role_permission = sa.text(
        "INSERT INTO service.role_permissions (role_code, permission_code) VALUES (:role_code, :permission_code)"
    )
    for role_code, permission_codes in _ROLE_PERMISSIONS.items():
        for permission_code in permission_codes:
            connection.execute(
                insert_role_permission.bindparams(role_code=role_code, permission_code=permission_code)
            )


def downgrade() -> None:
    op.drop_column("employees", "last_login_at", schema="service")
    op.drop_column("employees", "role_code", schema="service")
    op.drop_index(op.f("ix_service_consent_logs_employee_id"), table_name="consent_logs", schema="service")
    op.drop_table("consent_logs", schema="service")
    op.drop_table("role_permissions", schema="service")
    op.drop_table("permissions", schema="service")
    op.drop_table("roles", schema="service")
