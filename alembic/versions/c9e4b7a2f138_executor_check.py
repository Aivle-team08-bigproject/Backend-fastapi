"""stage_runs.executor 값 정리와 CHECK 제약

배경 — 2026-08-11 develop_aws 점검에서 나온 것.

Celery 를 걷어내면서 `pipeline_runs.celery_task_id` 는 `execution_id` 로 바뀌었지만
(`7b4d2e9f1a10`), `stage_runs.executor` 는 기본값이 `'CELERY'` 로 남아 있었고 재시도
경로에도 하드코딩돼 있었다. **Celery 가 없는데 "Celery 로 돌았다"는 기록이 쌓이던 상태.**

코드는 `5daae67` 에서 정리됐다. 그럼에도 CHECK 를 거는 이유는, 이번 일이 정확히
**DB 가 아무 문자열이나 받아서 코드가 틀려도 아무도 몰랐던** 경우이기 때문이다.
`config.py` 의 `Literal["AGENTCORE","AGENTCORE_DIRECT"]` 와 DB 를 일치시킨다.
`notices.status` · `email_deliveries.status` 와 같은 방침이다.

'CELERY' 는 허용값에 넣지 않는다. AWS RDS 의 service 스키마가 비어 있어 지금이
과거 데이터 없이 정리할 수 있는 유일한 시점이다.

Revision ID: c9e4b7a2f138
Revises: a3d7f21e9c84
Create Date: 2026-08-12
"""
from alembic import op

revision = "c9e4b7a2f138"
down_revision = "a3d7f21e9c84"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 방어용. AWS 는 0행이지만 로컬·과거 환경에는 남아 있을 수 있고,
    # 그대로 두면 아래 CHECK 추가가 실패한다.
    op.execute(
        "UPDATE service.stage_runs SET executor = 'AGENTCORE_DIRECT' WHERE executor = 'CELERY'"
    )
    op.create_check_constraint(
        "ck_stage_runs_executor",
        "stage_runs",
        "executor IN ('AGENTCORE', 'AGENTCORE_DIRECT')",
        schema="service",
    )


def downgrade() -> None:
    # 값은 되돌리지 않는다. 어느 행이 원래 'CELERY' 였는지 알 수 없고,
    # 되돌린다 해도 그 시점에 Celery 로 돌았다는 뜻이 되지 않는다.
    op.drop_constraint("ck_stage_runs_executor", "stage_runs", schema="service", type_="check")
