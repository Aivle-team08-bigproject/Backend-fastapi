"""widen email delivery type check to FINAL_ARTIFACT

f4c8a1e7b2d5 가 delivery_type 을 SELECTION_SAMPLE 하나로 좁히면서
"최종 산출물 발송(FINAL_ARTIFACT)을 붙일 때 이 CHECK 를 함께 넓힌다"고 남겼는데,
schema.py 의 Literal 만 넓어지고 이 마이그레이션이 빠졌다. 그 결과 최종 산출물
메일 발송이 INSERT 단계에서 CheckViolation 으로 죽고 화면에는 500 으로 보였다.

값의 정본은 Spring 의 DeliveryType(SELECTION_SAMPLE, FINAL_ARTIFACT)이며,
FastAPI schema.py 의 Literal 과 일치시킨다.

Revision ID: b6f3d0c9a271
Revises: 7b4d2e9f1a10
Create Date: 2026-08-13
"""
from alembic import op

revision = "b6f3d0c9a271"
down_revision = "7b4d2e9f1a10"
branch_labels = None
depends_on = None

CONSTRAINT = "ck_email_deliveries_type"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, "email_deliveries", schema="service", type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        "email_deliveries",
        "delivery_type IN ('SELECTION_SAMPLE','FINAL_ARTIFACT')",
        schema="service",
    )


def downgrade() -> None:
    # 되돌리려면 넓힌 값으로 쌓인 행이 먼저 정리돼야 한다. 남아 있으면 CHECK 생성이
    # 실패하므로, 조용히 지우지 않고 그대로 실패시킨다.
    op.drop_constraint(CONSTRAINT, "email_deliveries", schema="service", type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        "email_deliveries",
        "delivery_type IN ('SELECTION_SAMPLE')",
        schema="service",
    )
