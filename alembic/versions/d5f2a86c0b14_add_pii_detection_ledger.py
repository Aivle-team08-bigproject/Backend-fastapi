"""add pii detection ledger

Revision ID: d5f2a86c0b14
Revises: c8e3a1f75b20
Create Date: 2026-08-05

DLP(민감정보 탐지) 이력 테이블.

설계 원칙 — 탐지된 값 자체도, 값의 위치(offset)도 저장하지 않는다. 저장하면 이 테이블이
새로운 유출 지점이 되고 별도의 접근통제를 또 설계해야 한다. "무엇을 언제 어디서 몇 건
막았는가"는 detector_code + match_count 로 충분히 증명된다.

이 테이블이 따로 필요한 이유:
  1. BLOCK 은 요청 자체를 저장하지 않는다. 이력이 없으면 "막았다"는 사실이 남지 않는다.
  2. 오탐 조정 근거. 어떤 탐지기가 얼마나 걸리는지 모르면 정규식을 조일지 풀지 판단 불가.
  3. 기존 테이블에 못 얹는다. admin_audit_logs 는 관리자 조작 전용(actor/target 이
     employee_code)이고, pipeline_events 는 pipeline_run 에 매달리는데 BLOCK 은 run 이
     아예 생기지 않는다.

status 계열에 CHECK 를 걸지 않는 것은 service 스키마 전체 관행이다. 걸면 탐지기를 하나
추가할 때마다 마이그레이션이 필요해진다 — 값 검증은 애플리케이션 enum 이 한다.
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d5f2a86c0b14"
down_revision: str | None = "c8e3a1f75b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "service"


def upgrade() -> None:
    op.create_table(
        "pii_detections",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # --- 어디서 걸렸나 ---
        sa.Column("source_table", sa.String(length=64), nullable=False),
        sa.Column("source_column", sa.String(length=64), nullable=False),
        # BLOCK 은 원본 행이 저장되지 않으므로 참조할 id 가 없다. FK 를 걸지 않는 것도 같은 이유.
        sa.Column("source_id", sa.BigInteger(), nullable=True),
        sa.Column("source_endpoint", sa.String(length=200), nullable=False),
        # --- 무엇이 걸렸나 ---
        sa.Column("detector_code", sa.String(length=20), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        # 체크섬이 없는 탐지기(PHONE/EMAIL 등)는 NULL. false 와 구분해야 한다 —
        # false 는 "체크섬을 돌렸는데 틀렸다"이고 NULL 은 "돌릴 체크섬이 없다"이다.
        sa.Column("checksum_valid", sa.Boolean(), nullable=True),
        sa.Column("match_count", sa.Integer(), nullable=False),
        # --- 실제로 무슨 일이 일어났나 ---
        # severity 는 탐지기의 판정이고 action_taken 은 결과다. FLAG 를 사용자가
        # confirm 하고 진행한 경우(PROCEEDED_CONFIRMED)가 감사에서 가장 중요하다.
        sa.Column("action_taken", sa.String(length=24), nullable=False),
        # --- 누가 ---
        # POST /api/v1/data-requests 에 아직 인증이 없어 nullable 이다.
        # admin_audit_logs.actor_employee_code 가 NOT NULL 인 것과 다른 이유.
        sa.Column("actor_employee_code", sa.String(length=40), nullable=True),
        sa.Column("actor_ip", postgresql.INET(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_pii_detections"),
        schema=SCHEMA,
    )

    op.create_index(
        "ix_pii_detections_detected_at", "pii_detections", ["detected_at"], schema=SCHEMA
    )
    # 탐지기별 집계 — 오탐률을 보고 정규식을 조정하는 근거가 된다.
    op.create_index(
        "ix_pii_detections_detector_code", "pii_detections", ["detector_code"], schema=SCHEMA
    )
    # 특정 요청 한 건의 탐지 이력 조회.
    op.create_index(
        "ix_pii_detections_source", "pii_detections", ["source_table", "source_id"], schema=SCHEMA
    )

    op.execute("""
        COMMENT ON TABLE service.pii_detections IS
            'DLP 탐지 이력. 탐지된 값 자체는 저장하지 않는다(패턴 코드와 건수만).';
    """)

    # UPDATE/DELETE 는 주지 않는다 — 감사 이력은 append-only 다. 보존기간에 따른 파기는
    # 관리자 권한(portfolio_admin)으로 별도 배치가 수행한다.
    op.execute("""
        GRANT SELECT, INSERT ON service.pii_detections TO app_svc;
        GRANT USAGE, SELECT ON SEQUENCE service.pii_detections_id_seq TO app_svc;
        -- service 스키마에 ALTER DEFAULT PRIVILEGES 로 app_svc=arwd 가 걸려 있어
        -- 새 테이블에 UPDATE/DELETE 가 자동으로 붙는다(2026-08-05 Neon 실측).
        -- 이 이력은 append-only 여야 하므로 명시적으로 회수한다. BLOCK 은 원본을
        -- 저장하지 않기 때문에 이 테이블이 '막았다'는 사실의 유일한 증거다.
        -- 기본 권한이 없는 환경(AWS 신규 DB)에서는 no-op 이라 안전하다.
        REVOKE UPDATE, DELETE ON service.pii_detections FROM app_svc;
    """)


def downgrade() -> None:
    op.drop_index("ix_pii_detections_source", table_name="pii_detections", schema=SCHEMA)
    op.drop_index("ix_pii_detections_detector_code", table_name="pii_detections", schema=SCHEMA)
    op.drop_index("ix_pii_detections_detected_at", table_name="pii_detections", schema=SCHEMA)
    op.drop_table("pii_detections", schema=SCHEMA)
