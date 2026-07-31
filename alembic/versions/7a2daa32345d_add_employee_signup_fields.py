"""add employee signup fields (email, phone, position, consent, approval)

Revision ID: 7a2daa32345d
Revises: 9c81f4b7a2de
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "7a2daa32345d"
down_revision: str | None = "9c81f4b7a2de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("employees", sa.Column("email", sa.String(length=255), nullable=True), schema="service")
    op.add_column("employees", sa.Column("phone", sa.String(length=20), nullable=True), schema="service")
    op.add_column(
        "employees",
        sa.Column(
            "position",
            sa.Enum(
                "STAFF",
                "ASSISTANT_MANAGER",
                "MANAGER",
                "DEPUTY_GENERAL_MANAGER",
                "GENERAL_MANAGER",
                name="positiontype",
                native_enum=False,
                length=30,
            ),
            nullable=True,
        ),
        schema="service",
    )
    op.add_column("employees", sa.Column("terms_agreed_at", sa.DateTime(timezone=True), nullable=True), schema="service")
    op.add_column("employees", sa.Column("terms_version", sa.String(length=20), nullable=True), schema="service")
    op.add_column("employees", sa.Column("privacy_agreed_at", sa.DateTime(timezone=True), nullable=True), schema="service")
    op.add_column("employees", sa.Column("privacy_version", sa.String(length=20), nullable=True), schema="service")
    op.add_column("employees", sa.Column("approved_by", sa.String(length=40), nullable=True), schema="service")
    op.add_column("employees", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True), schema="service")
    op.add_column("employees", sa.Column("rejected_reason", sa.String(length=500), nullable=True), schema="service")

    # 기존에 이미 존재하는 직원(관리자가 만든 계정)은 이메일이 없다. 로그인 식별자가
    # employee_code에서 email로 바뀌므로, 기존 행을 임시 placeholder 이메일로 채워
    # NOT NULL + UNIQUE 제약을 걸 수 있게 한다. 이 값으로는 로그인할 수 없으므로
    # 운영에서는 마이그레이션 직후 관리자가 실제 이메일로 갱신해줘야 한다.
    op.execute(
        """
        UPDATE service.employees
        SET email = lower(employee_code) || '@migrated.invalid'
        WHERE email IS NULL
        """
    )

    op.alter_column("employees", "email", nullable=False, schema="service")
    op.create_index(
        op.f("ix_service_employees_email"), "employees", ["email"], unique=True, schema="service"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_service_employees_email"), table_name="employees", schema="service")
    op.drop_column("employees", "rejected_reason", schema="service")
    op.drop_column("employees", "approved_at", schema="service")
    op.drop_column("employees", "approved_by", schema="service")
    op.drop_column("employees", "privacy_version", schema="service")
    op.drop_column("employees", "privacy_agreed_at", schema="service")
    op.drop_column("employees", "terms_version", schema="service")
    op.drop_column("employees", "terms_agreed_at", schema="service")
    op.drop_column("employees", "position", schema="service")
    op.drop_column("employees", "phone", schema="service")
    op.drop_column("employees", "email", schema="service")
