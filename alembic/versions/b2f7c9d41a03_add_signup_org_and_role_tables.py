"""add signup: departments, roles, permissions, consent logs and employee columns

Revision ID: b2f7c9d41a03
Revises: a1c4e77b93f0
Create Date: 2026-07-31

회원가입 기능(자가가입 -> 관리자 승인)에 필요한 조직·역할·권한 구조를 추가한다.

1차 마이그레이션이라 employees.email 은 NULL 을 허용한다. 기존 계정 7개에 값을
채운 뒤 별도 리비전에서 NOT NULL 로 전환한다. 지금 NOT NULL 을 걸면 기존 계정이
전부 로그인 불가가 된다.

roles / permissions 의 값은 앱 enum(EmployeeRole, PermissionCode)과 반드시
일치해야 한다. 마스터 데이터라 이 리비전에서 함께 적재한다 — 비어 있으면
회원가입 화면에 선택할 부서가 없어 가입 자체가 불가능하다.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2f7c9d41a03"
down_revision: Union[str, None] = "a1c4e77b93f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "service"


def upgrade() -> None:
    # -----------------------------------------------------------------
    # 1. 조직
    # -----------------------------------------------------------------
    op.create_table(
        "departments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("code"),
        schema=SCHEMA,
    )

    # -----------------------------------------------------------------
    # 2. 역할 · 권한
    # -----------------------------------------------------------------
    op.create_table(
        "roles",
        sa.Column("role_code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=True),
        sa.Column("display_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("role_code"),
        schema=SCHEMA,
    )
    op.create_table(
        "permissions",
        sa.Column("permission_code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=True),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("display_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("permission_code"),
        schema=SCHEMA,
    )
    op.create_table(
        "role_permissions",
        sa.Column("role_code", sa.String(length=30), nullable=False),
        sa.Column("permission_code", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["role_code"], [f"{SCHEMA}.roles.role_code"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["permission_code"], [f"{SCHEMA}.permissions.permission_code"]),
        sa.PrimaryKeyConstraint("role_code", "permission_code"),
        schema=SCHEMA,
    )

    # -----------------------------------------------------------------
    # 3. 동의 이력
    # -----------------------------------------------------------------
    # employees 의 동의 컬럼은 "현재 상태" 캐시이고, 증빙은 이 테이블이 보관한다.
    # 약관 개정으로 재동의를 받으면 캐시는 덮어써지지만 이력은 남아야 한다.
    op.create_table(
        "consent_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("consent_type", sa.String(length=30), nullable=False),
        sa.Column("version", sa.String(length=20), nullable=False),
        sa.Column("agreed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["employee_id"], [f"{SCHEMA}.employees.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("idx_consent_logs_employee", "consent_logs", ["employee_id"], schema=SCHEMA)

    # -----------------------------------------------------------------
    # 4. employees 컬럼 추가 — 전부 NULL 허용
    # -----------------------------------------------------------------
    for col in (
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("department_id", sa.BigInteger(), nullable=True),
        sa.Column("position", sa.String(length=30), nullable=True),
        sa.Column("role_code", sa.String(length=30), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terms_agreed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terms_version", sa.String(length=20), nullable=True),
        sa.Column("privacy_agreed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("privacy_version", sa.String(length=20), nullable=True),
        sa.Column("approved_by", sa.String(length=40), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_reason", sa.String(length=500), nullable=True),
    ):
        op.add_column("employees", col, schema=SCHEMA)

    op.create_unique_constraint("uq_employees_email", "employees", ["email"], schema=SCHEMA)
    op.create_foreign_key(
        "fk_employees_department", "employees", "departments",
        ["department_id"], ["id"], source_schema=SCHEMA, referent_schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_employees_role", "employees", "roles",
        ["role_code"], ["role_code"], source_schema=SCHEMA, referent_schema=SCHEMA,
    )

    # -----------------------------------------------------------------
    # 5. 마스터 데이터
    # -----------------------------------------------------------------
    op.execute("""
        INSERT INTO service.departments (name, code) VALUES
            ('IT관리팀',     'IT_ADMIN'),
            ('데이터사업팀', 'DATA_BIZ'),
            ('데이터 운영팀','DATA_OPS'),
            ('개발팀',       'DEV'),
            ('영업팀',       'SALES'),
            ('인사팀',       'HR');
    """)

    op.execute("""
        INSERT INTO service.roles (role_code, name, description, display_order) VALUES
            ('ADMIN',   '관리자', '전체 권한. 가입 승인과 권한 부여를 담당한다.', 1),
            ('MANAGER', '책임자', '업무 전반을 처리하고 계약을 관리한다.',        2),
            ('SENIOR',  '선임',   '데이터 상품과 견적을 처리한다.',              3),
            ('GENERAL', '일반',   '조회 중심의 기본 권한.',                      4);
    """)

    # 앱의 PermissionCode / PERMISSION_DESCRIPTIONS 와 일치해야 한다.
    op.execute("""
        INSERT INTO service.permissions (permission_code, name, is_default, display_order) VALUES
            ('EMPLOYEE_READ',              '직원 조회',                        false, 1),
            ('EMPLOYEE_CREATE',            '직원 계정 생성',                   false, 2),
            ('EMPLOYEE_UPDATE',            '직원 정보 및 상태 변경',           false, 3),
            ('EMPLOYEE_PERMISSION_MANAGE', '직원 권한 변경',                   false, 4),
            ('EMPLOYEE_SESSION_MANAGE',    '직원 세션 조회 및 강제 로그아웃',  false, 5),
            ('AUDIT_READ',                 '감사 로그 조회',                   false, 6),
            ('DATA_PRODUCT_READ',          '데이터 상품 조회',                 true,  7),
            ('DATA_PRODUCT_WRITE',         '데이터 상품 등록 및 수정',         false, 8),
            ('QUOTE_READ',                 '견적 요청 조회',                   true,  9),
            ('QUOTE_PROCESS',              '견적 요청 처리',                   false, 10),
            ('CONTRACT_MANAGE',            '계약 관리',                        false, 11);
    """)

    # 역할별 기본 권한 템플릿. 승인 시 employee_permissions 로 복사된다.
    # 초기값이며 팀 합의에 따라 UPDATE 로 조정 가능하다.
    op.execute("""
        INSERT INTO service.role_permissions (role_code, permission_code)
        SELECT 'ADMIN', permission_code FROM service.permissions;

        INSERT INTO service.role_permissions (role_code, permission_code) VALUES
            ('MANAGER', 'EMPLOYEE_READ'),
            ('MANAGER', 'AUDIT_READ'),
            ('MANAGER', 'DATA_PRODUCT_READ'),
            ('MANAGER', 'DATA_PRODUCT_WRITE'),
            ('MANAGER', 'QUOTE_READ'),
            ('MANAGER', 'QUOTE_PROCESS'),
            ('MANAGER', 'CONTRACT_MANAGE'),
            ('SENIOR',  'EMPLOYEE_READ'),
            ('SENIOR',  'DATA_PRODUCT_READ'),
            ('SENIOR',  'DATA_PRODUCT_WRITE'),
            ('SENIOR',  'QUOTE_READ'),
            ('SENIOR',  'QUOTE_PROCESS'),
            ('GENERAL', 'DATA_PRODUCT_READ'),
            ('GENERAL', 'QUOTE_READ');
    """)

    # -----------------------------------------------------------------
    # 6. app_svc 권한 — 새 테이블에도 부여
    # -----------------------------------------------------------------
    op.execute("""
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON service.departments, service.roles, service.permissions,
               service.role_permissions, service.consent_logs
            TO app_svc;
        GRANT USAGE, SELECT ON SEQUENCE service.departments_id_seq  TO app_svc;
        GRANT USAGE, SELECT ON SEQUENCE service.consent_logs_id_seq TO app_svc;
    """)


def downgrade() -> None:
    op.drop_constraint("fk_employees_role", "employees", schema=SCHEMA, type_="foreignkey")
    op.drop_constraint("fk_employees_department", "employees", schema=SCHEMA, type_="foreignkey")
    op.drop_constraint("uq_employees_email", "employees", schema=SCHEMA, type_="unique")
    for name in (
        "rejected_reason", "approved_at", "approved_by",
        "privacy_version", "privacy_agreed_at", "terms_version", "terms_agreed_at",
        "last_login_at", "role_code", "position", "department_id", "phone", "email",
    ):
        op.drop_column("employees", name, schema=SCHEMA)

    op.drop_index("idx_consent_logs_employee", table_name="consent_logs", schema=SCHEMA)
    op.drop_table("consent_logs", schema=SCHEMA)
    op.drop_table("role_permissions", schema=SCHEMA)
    op.drop_table("permissions", schema=SCHEMA)
    op.drop_table("roles", schema=SCHEMA)
    op.drop_table("departments", schema=SCHEMA)
