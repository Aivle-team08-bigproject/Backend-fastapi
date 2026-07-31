"""normalize employees.department into a departments table

Revision ID: d62d1592c2e9
Revises: 7a2daa32345d
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d62d1592c2e9"
down_revision: str | None = "7a2daa32345d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 부서명이 자유 문자열로 흩어져 저장되는 걸 막기 위해 정규화한다. 최소한의 기본 부서 목록을
# 미리 만들어둔다 (회원가입 화면 드롭다운이 비어있지 않도록). 실제 조직에 맞게 나중에 SQL로
# 조정하면 된다 — 이번 범위에서는 관리자 CRUD API를 만들지 않기로 했다.
_BASELINE_DEPARTMENTS = [
    "IT관리팀",
    "데이터사업팀",
    "데이터 운영팀",
    "개발팀",
    "영업팀",
    "인사팀",
]


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("code"),
        schema="service",
    )

    insert_department = sa.text(
        "INSERT INTO service.departments (name, code, is_active, created_at, updated_at) "
        "VALUES (:name, :code, true, now(), now()) ON CONFLICT (name) DO NOTHING"
    )
    connection = op.get_bind()
    for name in _BASELINE_DEPARTMENTS:
        connection.execute(insert_department.bindparams(name=name, code=f"DEPT-{name}"))

    # 기존 employees.department 자유 문자열 중, 기본 목록에 없는 값도 부서로 만들어 데이터 손실을 막는다.
    op.execute(
        """
        WITH distinct_departments AS (
            SELECT DISTINCT department AS name
            FROM service.employees
            WHERE department IS NOT NULL AND btrim(department) <> ''
        )
        INSERT INTO service.departments (name, code, is_active, created_at, updated_at)
        SELECT name, 'DEPT-' || row_number() OVER (ORDER BY name), true, now(), now()
        FROM distinct_departments
        ON CONFLICT (name) DO NOTHING
        """
    )

    op.add_column(
        "employees",
        sa.Column(
            "department_id",
            sa.BigInteger(),
            sa.ForeignKey("service.departments.id"),
            nullable=True,
        ),
        schema="service",
    )
    op.execute(
        """
        UPDATE service.employees e
        SET department_id = d.id
        FROM service.departments d
        WHERE e.department = d.name
        """
    )
    op.create_index(
        op.f("ix_service_employees_department_id"),
        "employees",
        ["department_id"],
        unique=False,
        schema="service",
    )
    op.drop_column("employees", "department", schema="service")


def downgrade() -> None:
    op.add_column("employees", sa.Column("department", sa.String(length=100), nullable=True), schema="service")
    op.execute(
        """
        UPDATE service.employees e
        SET department = d.name
        FROM service.departments d
        WHERE e.department_id = d.id
        """
    )
    op.drop_index(op.f("ix_service_employees_department_id"), table_name="employees", schema="service")
    op.drop_column("employees", "department_id", schema="service")
    op.drop_table("departments", schema="service")
