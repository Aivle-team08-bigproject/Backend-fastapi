"""모델↔DB 드리프트 정리

`alembic/env.py` 에 `include_schemas` 가 없어서 `alembic check` 가 항상 실패했고,
그동안 아무도 드리프트를 볼 수 없었다. 그걸 고치고 나서 드러난 차이를 정리한다.

  ① requirements_analysis_runs 제거   PR #35 에서 코드가 사라졌는데 테이블만 남음
  ② employees.department_id 인덱스     모델엔 있고 DB 엔 없음 → 부서별 조회가 풀스캔
  ③ 인덱스 이름 정렬                   모델 기준으로 맞춤
  ④ consent_logs FK → RESTRICT        🔴 아래 참조

Revision ID: a3d7f21e9c84
Revises: 7b4d2e9f1a10
Create Date: 2026-08-11

⚠️ down_revision 이 처음엔 f4c8a1e7b2d5 였다. develop 에서 만들 당시 그것이 head 였는데,
   같은 시각 develop_aws 에서도 f4c8a1e7b2d5 위에 7b4d2e9f1a10(celery_task_id →
   execution_id)을 얹어 **체인이 갈라졌다.** 양쪽 변경 대상이 겹치지 않아 순서를 어떻게
   잡아도 결과가 같으므로, 나중에 온 이 리비전을 뒤로 보내 선형으로 되돌린다.

   AWS RDS 에는 이 리비전이 먼저 적용돼 있었다. 7b4d2e9f1a10 의 rename 두 줄을 수동으로
   맞춘 뒤 이 순서로 정리했다 — alembic 은 head 가 적용됐으면 그 앞도 적용된 것으로
   보므로 alembic_version 은 건드리지 않는다.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a3d7f21e9c84"
down_revision = "7b4d2e9f1a10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------------
    # ① 고아 테이블 제거
    #
    # PR #35 에서 automation 도메인이 통째로 빠지면서 이 테이블을 쓰는 코드가
    # 사라졌다. 다만 alembic 리비전이 없어 테이블은 남았고, AWS 신규 구축에서도
    # baseline 이 그대로 만들어 냈다.
    # ---------------------------------------------------------------
    op.drop_index("ix_service_requirements_analysis_runs_requested_by",
                  table_name="requirements_analysis_runs", schema="service")
    op.drop_index("ix_service_requirements_analysis_runs_status",
                  table_name="requirements_analysis_runs", schema="service")
    op.drop_table("requirements_analysis_runs", schema="service")

    # ---------------------------------------------------------------
    # ② employees.department_id 인덱스
    #
    # 모델에는 index=True 가 있는데 DB 에는 없었다. 지금은 행이 적어 티가 안 나지만
    # 부서별 직원 조회가 풀스캔이다.
    # ---------------------------------------------------------------
    op.create_index("ix_service_employees_department_id", "employees",
                    ["department_id"], schema="service")

    # ---------------------------------------------------------------
    # ③ 인덱스·제약 이름을 모델 기준으로 정렬
    #
    # 기능은 같고 이름만 달랐다. 맞춰두지 않으면 `alembic check` 가 매번 차이를
    # 보고해서 진짜 드리프트가 묻힌다.
    #
    # employees.email 은 DB 가 UNIQUE 제약, 모델이 UNIQUE 인덱스였다. PostgreSQL 에서
    # 둘은 사실상 같지만(제약이 내부적으로 인덱스를 만든다) 이름이 달라 감지된다.
    # 제약을 떼고 인덱스로 다시 만든다 — 데이터가 없는 지금이 가장 안전한 시점이다.
    # ---------------------------------------------------------------
    op.execute("ALTER INDEX service.idx_consent_logs_employee "
               "RENAME TO ix_service_consent_logs_employee_id")

    op.drop_constraint("uq_employees_email", "employees", schema="service", type_="unique")
    op.create_index("ix_service_employees_email", "employees", ["email"],
                    unique=True, schema="service")

    # ---------------------------------------------------------------
    # ④ 🔴 consent_logs FK 를 CASCADE → RESTRICT
    #
    # 동의 이력은 법적 증빙이다(신용정보법상 동의 기록). 그런데 FK 가 CASCADE 라
    # **직원 행을 DELETE 하면 동의 이력이 같이 사라진다.**
    #
    # 지금은 직원 삭제가 소프트 삭제(status=DISABLED)라 발동하지 않는다. 다만
    # 하드 삭제 경로가 생기는 순간 조용히 사라지고, 그때는 알아차리기 어렵다.
    # app_svc 에서 DELETE 권한을 회수해 둔 것(V015)도 FK CASCADE 로 우회되면
    # 의미가 없어진다.
    #
    # RESTRICT 면 참조가 남아 있는 직원은 삭제 자체가 거부된다. 파기가 필요하면
    # 보존기간 정책에 따라 hanacard_admin 권한의 배치가 순서대로 지운다.
    #
    # role_permissions 의 CASCADE 는 그대로 둔다 — 역할을 지우면 그 역할의 권한
    # 매핑도 사라지는 게 맞고, 감사 자료가 아니다.
    # ---------------------------------------------------------------
    op.drop_constraint("consent_logs_employee_id_fkey", "consent_logs",
                       schema="service", type_="foreignkey")
    op.create_foreign_key("consent_logs_employee_id_fkey", "consent_logs", "employees",
                          ["employee_id"], ["id"],
                          source_schema="service", referent_schema="service",
                          ondelete="RESTRICT")


def downgrade() -> None:
    op.drop_constraint("consent_logs_employee_id_fkey", "consent_logs",
                       schema="service", type_="foreignkey")
    op.create_foreign_key("consent_logs_employee_id_fkey", "consent_logs", "employees",
                          ["employee_id"], ["id"],
                          source_schema="service", referent_schema="service",
                          ondelete="CASCADE")

    op.drop_index("ix_service_employees_email", table_name="employees", schema="service")
    op.create_unique_constraint("uq_employees_email", "employees", ["email"], schema="service")

    op.execute("ALTER INDEX service.ix_service_consent_logs_employee_id "
               "RENAME TO idx_consent_logs_employee")

    op.drop_index("ix_service_employees_department_id",
                  table_name="employees", schema="service")

    op.create_table(
        "requirements_analysis_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("raw_request", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("model_provider", sa.String(length=50), nullable=False),
        sa.Column("model_id", sa.String(length=100), nullable=False),
        sa.Column("analysis_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema="service",
    )
    op.create_index("ix_service_requirements_analysis_runs_requested_by",
                    "requirements_analysis_runs", ["requested_by"], schema="service")
    op.create_index("ix_service_requirements_analysis_runs_status",
                    "requirements_analysis_runs", ["status"], schema="service")
