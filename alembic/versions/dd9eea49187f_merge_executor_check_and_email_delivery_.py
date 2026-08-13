"""갈라진 두 head 를 합친다 (스키마 변경 없음)

`c9e4b7a2f138`(stage_runs.executor CHECK) 와 `b6f3d0c9a271`(email_deliveries
delivery_type CHECK 확장) 이 둘 다 `7b4d2e9f1a10` 을 부모로 잡아 head 가 2개가 됐다.
그 상태로는 `alembic upgrade head` 가 "Multiple head revisions are present" 로
죽는다 — CI 의 마이그레이션 단계가 이것 때문에 실패했다.

원인은 브랜치 분기가 아니다. `b6f3d0c9a271` 은 `c9e4b7a2f138` 이 이미 트리에 있는
커밋(`5df17cf`) 위에서 만들어졌는데 down_revision 만 3단 아래를 가리켰다. 즉 값이
잘못 적힌 것이고, git 히스토리는 처음부터 일직선이었다.

그렇다면 `b6f3d0c9a271` 의 down_revision 을 `c9e4b7a2f138` 로 고쳐 선형으로 되돌리는
편이 사실에 가깝다. 그렇게 하지 않는 이유는 **Neon 공용 DB 에 두 리비전이 모두 적용돼
있기 때문**이다(`service.alembic_version` 에 두 행이 stamp 돼 있었다). 부모를 고치면
그 DB 의 stamp 가 존재하지 않는 경로를 가리키게 되어 이후 upgrade 가 깨진다. 이미
적용된 리비전의 부모는 바꾸지 않는다.

두 갈래가 건드리는 객체는 겹치지 않는다 — 한쪽은 stage_runs, requirements_analysis_runs,
employees, consent_logs 이고 다른 쪽은 email_deliveries 뿐이다. 적용 순서와 무관하게
결과가 같으므로 이 리비전은 그래프만 합치고 스키마는 건드리지 않는다.

이 리비전을 적용하면 Neon 의 alembic_version 두 행은 한 행으로 접힌다.

Revision ID: dd9eea49187f
Revises: c9e4b7a2f138, b6f3d0c9a271
Create Date: 2026-08-13
"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "dd9eea49187f"
down_revision: Union[str, Sequence[str], None] = ("c9e4b7a2f138", "b6f3d0c9a271")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 그래프 병합 전용. 양쪽 갈래가 각자 자기 변경을 이미 끝냈다.
    pass


def downgrade() -> None:
    # 되돌리면 head 가 다시 둘로 갈라진다. 그게 이 리비전의 유일한 효과다.
    pass
