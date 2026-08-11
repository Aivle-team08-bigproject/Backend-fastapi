"""add service.notices

공지사항 테이블. 백엔드 요청(NOTICE_DB_CHANGE_FOR_ADMIN.md, 2026-08-09)을
service 스키마 관례에 맞춰 구현한다.

원안은 raw SQL 이었으나 alembic 리비전으로 옮겼다. service 는 alembic 이
관리하는 스키마라, DB 에 직접 만들면 AWS 이전 시 재구축 순서
(V001~V005 → alembic upgrade head → V006~V014)에서 빠져 테이블이 사라진다.

Revision ID: e2a91d4f6c38
Revises: 3f8e1c2a7b90
Create Date: 2026-08-09
"""
from alembic import op
import sqlalchemy as sa

revision = "e2a91d4f6c38"
down_revision = "3f8e1c2a7b90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notices",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("created_by_employee_id", sa.BigInteger(), nullable=False),
        sa.Column("updated_by_employee_id", sa.BigInteger(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by_employee_id"], ["service.employees.id"],
                                name="fk_notices_created_by"),
        sa.ForeignKeyConstraint(["updated_by_employee_id"], ["service.employees.id"],
                                name="fk_notices_updated_by"),
        sa.CheckConstraint("status IN ('DRAFT','PUBLISHED','ARCHIVED')",
                           name="ck_notices_status"),
        # DRAFT 는 게시 전이라 게시시각이 없어야 하고, PUBLISHED·ARCHIVED 는
        # 게시된 적이 있으므로 있어야 한다.
        #
        # 이 제약이 막는 것:
        #   ① "PUBLISHED 인데 published_at 이 NULL" — 아래 목록 인덱스가
        #      published_at 순으로 정렬하므로 그런 행은 맨 뒤로 밀려
        #      "올렸는데 안 보인다"로 나타난다.
        #   ② PUBLISHED → DRAFT 되돌리기 — 게시 취소는 ARCHIVED 로 간다.
        #      DRAFT 는 "한 번도 게시된 적 없음"의 뜻으로 고정한다.
        sa.CheckConstraint(
            "(status = 'DRAFT' AND published_at IS NULL) OR "
            "(status <> 'DRAFT' AND published_at IS NOT NULL)",
            name="ck_notices_published_at_consistent"),
        schema="service",
    )

    op.create_index("idx_notices_created_by", "notices",
                    ["created_by_employee_id"], schema="service")
    op.create_index("idx_notices_updated_by", "notices",
                    ["updated_by_employee_id"], schema="service")
    # 목록 조회: 게시된 공지를 최신순으로.
    # published_at 기준인 이유 — updated_at 으로 정렬하면 3년 전 공지의 오타를
    # 고치는 순간 그 공지가 목록 맨 위로 올라온다.
    # id DESC 는 동일 시각 tie-break 겸 keyset 페이징용.
    op.create_index("idx_notices_status_published", "notices",
                    ["status", sa.text("published_at DESC"), sa.text("id DESC")],
                    schema="service")

    # updated_at 자동 갱신.
    #
    # 이 함수는 로컬 개발 DB 에만 있고 Neon 에는 없었다 — 정본 SQL 에도 alembic 에도
    # 정의가 없어, 과거에 수작업으로 만들어진 것으로 보인다. 그래서 여기서 만든다.
    # CREATE OR REPLACE 라 이미 있는 환경에서도 안전하고, 본문이 동일해 기존
    # 6개 트리거(employees·clients·contracts·data_requests·pipeline_runs·
    # task_view_snapshots)의 동작도 바뀌지 않는다.
    #
    # downgrade 에서 함수를 지우지 않는 이유: 다른 테이블이 쓰고 있을 수 있다.
    op.execute(
        "CREATE OR REPLACE FUNCTION service.trigger_set_updated_at() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN NEW.updated_at = now(); RETURN NEW; END; $$"
    )
    op.execute(
        "CREATE TRIGGER set_updated_at_notices "
        "BEFORE UPDATE ON service.notices "
        "FOR EACH ROW EXECUTE FUNCTION service.trigger_set_updated_at()"
    )

    # 소프트 삭제(ARCHIVED)만 허용한다.
    # service 스키마에 ALTER DEFAULT PRIVILEGES 가 걸려 있어 portfolio_admin 이
    # 만든 테이블은 app_svc 에 arwd(SELECT·INSERT·UPDATE·DELETE)가 자동 부여된다.
    # 그래서 DELETE 만 명시적으로 회수한다. UPDATE 는 상태 변경·본문 수정에 필요.
    op.execute("REVOKE DELETE ON service.notices FROM app_svc")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS set_updated_at_notices ON service.notices")
    op.drop_index("idx_notices_status_published", "notices", schema="service")
    op.drop_index("idx_notices_updated_by", "notices", schema="service")
    op.drop_index("idx_notices_created_by", "notices", schema="service")
    op.drop_table("notices", schema="service")
