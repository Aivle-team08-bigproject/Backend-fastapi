"""add structured work intake and contract delivery date

Revision ID: 3f8e1c2a7b90
Revises: d5f2a86c0b14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "3f8e1c2a7b90"
down_revision: str | None = "d5f2a86c0b14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "data_requests",
        sa.Column("data_sensitivity", sa.String(length=20), nullable=False, server_default="UNKNOWN"),
        schema="service",
    )
    op.add_column(
        "contracts",
        sa.Column("delivery_due_at", sa.DateTime(timezone=True), nullable=True),
        schema="service",
    )


def downgrade() -> None:
    op.drop_column("contracts", "delivery_due_at", schema="service")
    op.drop_column("data_requests", "data_sensitivity", schema="service")
