"""drop orphaned requirements_analysis_runs

Revision ID: e7a3c05d1928
Revises: 3f8e1c2a7b90
Create Date: 2026-08-07

PR #35 에서 `app/domains/automation/` 이 통째로 제거되면서 모델과 `alembic/env.py`
의 import 가 함께 사라졌는데, 테이블은 DB 에 그대로 남았다.

이대로 두면 다음에 누가 `alembic revision --autogenerate` 를 돌릴 때
"메타데이터엔 없는데 DB 엔 있다" 로 판단해 엉뚱한 위치의 리비전에
`op.drop_table('requirements_analysis_runs')` 를 끼워 넣는다. 의도한 변경과
섞이면 리뷰에서 놓치기 쉬우므로, 여기서 명시적으로 정리한다.

적용 전 실측(2026-08-07 Neon):
    to_regclass  service.requirements_analysis_runs   존재
    행 수        0
    참조 FK      없음
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7a3c05d1928"
down_revision: str | None = "3f8e1c2a7b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "service"
TABLE = "requirements_analysis_runs"


def upgrade() -> None:
    op.drop_index(f"ix_service_{TABLE}_status", table_name=TABLE, schema=SCHEMA)
    op.drop_index(f"ix_service_{TABLE}_requested_by", table_name=TABLE, schema=SCHEMA)
    op.drop_table(TABLE, schema=SCHEMA)


def downgrade() -> None:
    # 13713dc07ce8 베이스라인의 정의를 그대로 되살린다.
    # status 는 native_enum=False 라 VARCHAR + CHECK 로 만들어지므로
    # 별도로 DROP TYPE 할 enum 타입이 없다.
    op.create_table(
        TABLE,
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("raw_request", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            sa.Enum("SUCCEEDED", "FAILED", name="analysisstatus", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("model_provider", sa.String(length=50), nullable=False),
        sa.Column("model_id", sa.String(length=100), nullable=False),
        sa.Column("analysis_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_service_{TABLE}_requested_by", TABLE, ["requested_by"], unique=False, schema=SCHEMA
    )
    op.create_index(f"ix_service_{TABLE}_status", TABLE, ["status"], unique=False, schema=SCHEMA)
