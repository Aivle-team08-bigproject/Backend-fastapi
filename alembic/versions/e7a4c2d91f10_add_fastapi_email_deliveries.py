"""add FastAPI-owned email delivery state

Spring is a queue consumer in the B architecture and does not own this table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "e7a4c2d91f10"
down_revision: str | None = "3f8e1c2a7b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_deliveries",
        sa.Column("delivery_id", sa.String(length=36), primary_key=True),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("stage_attempt_no", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("requested_by", sa.BigInteger(), nullable=True),
        sa.Column("delivery_type", sa.String(length=40), nullable=False, server_default="SELECTION_SAMPLE"),
        sa.Column("recipient", sa.String(length=254), nullable=False),
        sa.Column("recipient_normalized", sa.String(length=254), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="QUEUED"),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("sample_sha256", sa.String(length=64), nullable=True),
        sa.Column("template_version", sa.String(length=50), nullable=True),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.Column("attempt_count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["service.pipeline_runs.id"]),
        sa.ForeignKeyConstraint(["requested_by"], ["service.employees.id"]),
        schema="service",
    )
    op.create_unique_constraint(
        "uq_email_deliveries_idempotency_key",
        "email_deliveries",
        ["idempotency_key"],
        schema="service",
    )
    op.create_index(
        "ix_service_email_deliveries_status",
        "email_deliveries",
        ["status"],
        schema="service",
    )
    op.create_index(
        "ix_service_email_deliveries_run_status",
        "email_deliveries",
        ["run_id", "status"],
        schema="service",
    )
    op.create_index(
        "uq_email_deliveries_active_target",
        "email_deliveries",
        ["run_id", "stage_attempt_no", "recipient_normalized", "delivery_type"],
        unique=True,
        schema="service",
        postgresql_where=sa.text("status IN ('QUEUED', 'SENDING')"),
    )


def downgrade() -> None:
    op.drop_index("uq_email_deliveries_active_target", table_name="email_deliveries", schema="service")
    op.drop_index("ix_service_email_deliveries_run_status", table_name="email_deliveries", schema="service")
    op.drop_index("ix_service_email_deliveries_status", table_name="email_deliveries", schema="service")
    op.drop_constraint("uq_email_deliveries_idempotency_key", "email_deliveries", schema="service", type_="unique")
    op.drop_table("email_deliveries", schema="service")
