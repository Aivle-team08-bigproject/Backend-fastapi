"""rename pipeline execution correlation column

Revision ID: 7b4d2e9f1a10
Revises: f4c8a1e7b2d5
"""

from typing import Sequence, Union

from alembic import op


revision: str = "7b4d2e9f1a10"
down_revision: Union[str, Sequence[str], None] = "f4c8a1e7b2d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "pipeline_runs",
        "celery_task_id",
        new_column_name="execution_id",
        schema="service",
    )
    op.execute(
        "ALTER INDEX IF EXISTS service.ix_service_pipeline_runs_celery_task_id "
        "RENAME TO ix_service_pipeline_runs_execution_id"
    )


def downgrade() -> None:
    op.alter_column(
        "pipeline_runs",
        "execution_id",
        new_column_name="celery_task_id",
        schema="service",
    )
    op.execute(
        "ALTER INDEX IF EXISTS service.ix_service_pipeline_runs_execution_id "
        "RENAME TO ix_service_pipeline_runs_celery_task_id"
    )
