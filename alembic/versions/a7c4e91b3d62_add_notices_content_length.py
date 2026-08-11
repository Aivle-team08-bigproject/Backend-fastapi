"""add notices content length limit

백엔드 요청(docs/NOTICE_DB_FEEDBACK_ACCEPTANCE_PLAN.md, 2026-08-09):
content 최대 길이를 API·Frontend 가 이미 쓰는 20,000자로 확정하고 DB 에도 건다.
앱의 Pydantic 검증과 이중으로 두는 이유는, 앱을 우회하는 경로(배치·직접 SQL)에서도
지켜져야 하기 때문이다.

e2a91d4f6c38 이 이미 Neon 에 적용됐으므로 그 리비전을 고치지 않고 새로 붙인다.

Revision ID: a7c4e91b3d62
Revises: e2a91d4f6c38
Create Date: 2026-08-10
"""
from alembic import op

revision = "a7c4e91b3d62"
down_revision = "e2a91d4f6c38"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_notices_content_length",
        "notices",
        "char_length(content) <= 20000",
        schema="service",
    )


def downgrade() -> None:
    op.drop_constraint("ck_notices_content_length", "notices", schema="service")
