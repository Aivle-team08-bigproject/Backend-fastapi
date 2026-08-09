"""add notices

Revision ID: e4a1b2c3d4e5
Revises: 3f8e1c2a7b90
"""

from alembic import op
import sqlalchemy as sa

revision = "e4a1b2c3d4e5"
down_revision = "3f8e1c2a7b90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notices",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by_employee_id", sa.BigInteger(), nullable=False),
        sa.Column("updated_by_employee_id", sa.BigInteger(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')", name="ck_notices_status"),
        sa.ForeignKeyConstraint(["created_by_employee_id"], ["service.employees.id"]),
        sa.ForeignKeyConstraint(["updated_by_employee_id"], ["service.employees.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="service",
    )
    op.create_index("ix_notices_created_by_employee_id", "notices", ["created_by_employee_id"], schema="service")
    op.create_index("ix_notices_updated_by_employee_id", "notices", ["updated_by_employee_id"], schema="service")
    op.create_index(
        "ix_notices_status_published_at_id",
        "notices",
        ["status", "published_at", "id"],
        schema="service",
    )


def downgrade() -> None:
    op.drop_index("ix_notices_status_published_at_id", table_name="notices", schema="service")
    op.drop_index("ix_notices_updated_by_employee_id", table_name="notices", schema="service")
    op.drop_index("ix_notices_created_by_employee_id", table_name="notices", schema="service")
    op.drop_table("notices", schema="service")
