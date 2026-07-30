"""add pipeline run supervisor columns

Supervisor가 검증 실패·반려 사유(error_message)와 되돌아갈 단계(rollback_to_stage)를
run 단위로 들고 있어야 다음 dispatch에서 그 단계부터 재개할 수 있다.

Revision ID: a1c4e77b93f0
Revises: 9c81f4b7a2de
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "a1c4e77b93f0"
down_revision: str | None = "9c81f4b7a2de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pipeline_runs",
        sa.Column("error_message", sa.Text(), nullable=True),
        schema="service",
    )
    op.add_column(
        "pipeline_runs",
        sa.Column("rollback_to_stage", sa.String(length=80), nullable=True),
        schema="service",
    )


def downgrade() -> None:
    op.drop_column("pipeline_runs", "rollback_to_stage", schema="service")
    op.drop_column("pipeline_runs", "error_message", schema="service")
