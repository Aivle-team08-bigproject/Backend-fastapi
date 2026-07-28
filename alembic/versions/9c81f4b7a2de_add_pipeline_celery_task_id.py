"""add pipeline celery task id

Revision ID: 9c81f4b7a2de
Revises: 13713dc07ce8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "9c81f4b7a2de"
down_revision: str | None = "13713dc07ce8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pipeline_runs",
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        schema="service",
    )
    op.create_index(
        "ix_service_pipeline_runs_celery_task_id",
        "pipeline_runs",
        ["celery_task_id"],
        unique=True,
        schema="service",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_service_pipeline_runs_celery_task_id",
        table_name="pipeline_runs",
        schema="service",
    )
    op.drop_column("pipeline_runs", "celery_task_id", schema="service")
