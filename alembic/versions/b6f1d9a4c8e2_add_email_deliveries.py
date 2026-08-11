"""add email_deliveries table (B안 발송 이력)

Revision ID: b6f1d9a4c8e2
Revises: a7c4e91b3d62

주의: 이 revision의 down_revision(a7c4e91b3d62, notices content length)은 develop
브랜치 계보다. kimjounggun 브랜치 자체 최신 head는 3f8e1c2a7b90(work intake
fields)였는데, email_deliveries 모델(app/domains/pipeline/model.py의
EmailDelivery)을 추가한 SQS adapter 작업에 마이그레이션이 누락돼 있었다.
2026-08-10에 develop에서 포크한 새 Neon 브랜치를 테스트용으로 쓰면서 그 계보
위에 이 revision을 얹었다. kimjounggun을 develop에 merge/rebase할 때 이
down_revision을 실제 병합 시점의 kimjounggun 쪽 head로 다시 맞출 것.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "b6f1d9a4c8e2"
down_revision: str | None = "a7c4e91b3d62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_deliveries",
        sa.Column("delivery_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "run_id",
            sa.BigInteger(),
            sa.ForeignKey("service.pipeline_runs.id"),
            nullable=False,
        ),
        sa.Column("stage_attempt_no", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column(
            "requested_by",
            sa.BigInteger(),
            sa.ForeignKey("service.employees.id"),
            nullable=True,
        ),
        sa.Column(
            "delivery_type",
            sa.String(length=40),
            nullable=False,
            server_default="SELECTION_SAMPLE",
        ),
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
        sa.UniqueConstraint("idempotency_key", name="uq_email_deliveries_idempotency_key"),
        schema="service",
    )
    op.create_index(
        "ix_email_deliveries_status",
        "email_deliveries",
        ["status"],
        schema="service",
    )
    op.create_index(
        "ix_email_deliveries_run_status",
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
    op.drop_index("ix_email_deliveries_run_status", table_name="email_deliveries", schema="service")
    op.drop_index("ix_email_deliveries_status", table_name="email_deliveries", schema="service")
    op.drop_table("email_deliveries", schema="service")
