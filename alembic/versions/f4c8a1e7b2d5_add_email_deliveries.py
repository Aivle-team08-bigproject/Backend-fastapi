"""add service.email_deliveries

이메일 발송 이력. B안 구조에서 FastAPI 가 소유하고 Spring 은 큐 소비자로만
동작하므로, 이 테이블은 service 스키마(=alembic 관리)에 둔다.

원안은 PR #49 의 e7a4c2d91f10 이었으나 팀원이 커밋 1484741 에서 제거했다.
DB 변경은 DB 담당이 만든다는 합의에 따라 여기서 다시 만든다.
컬럼 구성은 app/domains/pipeline/model.py 의 EmailDelivery 를 정본으로 삼되,
service 스키마 관례(CHECK · server_default · updated_at 트리거)를 더했다.

Revision ID: f4c8a1e7b2d5
Revises: a7c4e91b3d62
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa

revision = "f4c8a1e7b2d5"
down_revision = "a7c4e91b3d62"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_deliveries",
        # UUID 를 문자열로 받는다. 모델이 default=lambda: str(uuid4()) 라
        # 값 생성 주체가 애플리케이션이므로 DB 기본값은 두지 않는다.
        sa.Column("delivery_id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("stage_attempt_no", sa.SmallInteger(), nullable=False,
                  server_default="1"),
        sa.Column("requested_by", sa.BigInteger(), nullable=True),
        sa.Column("delivery_type", sa.String(40), nullable=False,
                  server_default="SELECTION_SAMPLE"),
        # 🔴 개인정보. 보존기간·파기 주체가 아직 미정이다(아래 COMMENT 참조).
        sa.Column("recipient", sa.String(254), nullable=False),
        sa.Column("recipient_normalized", sa.String(254), nullable=False),
        sa.Column("status", sa.String(30), nullable=False,
                  server_default="QUEUED"),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("sample_sha256", sa.String(64), nullable=True),
        sa.Column("template_version", sa.String(50), nullable=True),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("failure_code", sa.String(80), nullable=True),
        sa.Column("attempt_count", sa.SmallInteger(), nullable=False,
                  server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["service.pipeline_runs.id"],
                                name="fk_email_deliveries_run"),
        sa.ForeignKeyConstraint(["requested_by"], ["service.employees.id"],
                                name="fk_email_deliveries_requested_by"),
        # 모델의 EmailDeliveryStatus 7종.
        # SENT 는 "SES 가 접수했다", DELIVERED 는 "수신함에 들어갔다"로 뜻이 다르다.
        sa.CheckConstraint(
            "status IN ('QUEUED','SENDING','SENT','DELIVERED',"
            "'FAILED','BOUNCED','COMPLAINT')",
            name="ck_email_deliveries_status"),
        # 현재 코드가 처리하는 유형은 하나뿐이다.
        # schema.py 가 Literal["SELECTION_SAMPLE"] 로 좁혀 놓았고, DB 가 먼저
        # 열어 주면 코드가 못 다루는 값이 들어온다.
        # 최종 산출물 발송(FINAL_ARTIFACT)을 붙일 때 이 CHECK 를 함께 넓힌다.
        sa.CheckConstraint(
            "delivery_type IN ('SELECTION_SAMPLE')",
            name="ck_email_deliveries_type"),
        # 발송이 끝난 건에만 시각이 있어야 한다.
        # 이 제약이 막는 것: QUEUED 인데 delivered_at 이 찍혀 화면에 "전달 완료"로
        # 보이는 상태. 반대로 DELIVERED 인데 시각이 없으면 정렬에서 밀린다.
        sa.CheckConstraint(
            "(status = 'DELIVERED') = (delivered_at IS NOT NULL)",
            name="ck_email_deliveries_delivered_at_consistent"),
        sa.CheckConstraint("attempt_count >= 0",
                           name="ck_email_deliveries_attempt_count"),
        schema="service",
    )

    # 멱등성. 클라이언트가 준 Idempotency-Key 를 전역 유니크로 잡는다.
    # service.py 가 같은 키에 다른 fingerprint 가 오면 409 로 막는다.
    op.create_unique_constraint(
        "uq_email_deliveries_idempotency_key", "email_deliveries",
        ["idempotency_key"], schema="service")

    # 이름은 model.py 의 Index() 선언을 그대로 따른다.
    # 다르게 지으면 이후 autogenerate 가 DROP/CREATE 를 반복 제안한다.
    op.create_index("ix_email_deliveries_run_status", "email_deliveries",
                    ["run_id", "status"], schema="service")
    # status 단일 인덱스는 모델의 index=True 가 만드는 이름과 맞춘다.
    op.create_index("ix_service_email_deliveries_status", "email_deliveries",
                    ["status"], schema="service")

    # 진행 중인 발송이 있으면 같은 대상에 또 못 넣는다 = 중복 발송 방지.
    #
    # ⚠️ 이 인덱스는 status 가 QUEUED·SENDING 일 때만 유효하다.
    #    email_retry_worker 가 정체 건을 FAILED 로 바꾸면 이 인덱스에서 빠져
    #    같은 대상에 재요청이 통과한다. Spring 이 이미 SES 로 보낸 뒤 결과 반영만
    #    늦은 경우 고객이 두 번 받는다. 회신 문서에서 함께 제기했다.
    op.create_index(
        "uq_email_deliveries_active_target", "email_deliveries",
        ["run_id", "stage_attempt_no", "recipient_normalized", "delivery_type"],
        unique=True, schema="service",
        postgresql_where=sa.text("status IN ('QUEUED', 'SENDING')"))

    # updated_at 자동 갱신. service 스키마의 다른 7개 테이블과 동일하다.
    #
    # 이 트리거가 필요한 이유가 여기서는 하나 더 있다 — email_retry_worker 가
    # `updated_at < cutoff` 로 정체 건을 찾는데, 상태를 바꾸는 쪽이 updated_at 을
    # 직접 대입하지 않으면 판정 기준이 낡은 값으로 남는다. DB 가 갱신하면
    # 갱신 주체가 누구든 기준이 어긋나지 않는다.
    op.execute(
        "CREATE OR REPLACE FUNCTION service.trigger_set_updated_at() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN NEW.updated_at = now(); RETURN NEW; END; $$"
    )
    op.execute(
        "CREATE TRIGGER set_updated_at_email_deliveries "
        "BEFORE UPDATE ON service.email_deliveries "
        "FOR EACH ROW EXECUTE FUNCTION service.trigger_set_updated_at()"
    )

    # 개인정보 표시. 파기 배치를 어디에 두든 대상 컬럼이 문서로 남아야 한다.
    # notices 와 달리 DELETE 를 회수하지 않는 이유가 이것이다 —
    # recipient 파기는 행 삭제(또는 마스킹 UPDATE)로 이뤄진다.
    op.execute(
        "COMMENT ON COLUMN service.email_deliveries.recipient IS "
        "'개인정보(이메일 주소). 보존기간·파기 주체 미정 — 2026-08-10 기준 협의 중. "
        "파기는 created_at 기준 배치로 수행한다'"
    )
    op.execute(
        "COMMENT ON COLUMN service.email_deliveries.recipient_normalized IS "
        "'개인정보(정규화된 이메일 주소). 중복 발송 판정용. recipient 와 함께 파기한다'"
    )
    op.execute(
        "COMMENT ON COLUMN service.email_deliveries.attempt_count IS "
        "'재시도 횟수. 2026-08-10 기준 대입하는 코드가 없다(항상 0). "
        "큐 백엔드가 재전달을 소유하므로, 사용 여부가 정해지면 갱신 주체를 명시할 것'"
    )
    op.execute(
        "COMMENT ON TABLE service.email_deliveries IS "
        "'이메일 발송 이력. FastAPI 소유이며 Spring 은 큐로만 접근한다'"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS set_updated_at_email_deliveries "
               "ON service.email_deliveries")
    op.drop_index("uq_email_deliveries_active_target", "email_deliveries",
                  schema="service")
    op.drop_index("ix_service_email_deliveries_status", "email_deliveries",
                  schema="service")
    op.drop_index("ix_email_deliveries_run_status", "email_deliveries",
                  schema="service")
    op.drop_constraint("uq_email_deliveries_idempotency_key", "email_deliveries",
                       schema="service", type_="unique")
    op.drop_table("email_deliveries", schema="service")
