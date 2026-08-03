"""finalize signup schema: 부서 정규화 마무리, email 을 로그인 키로 확정

Revision ID: c8e3a1f75b20
Revises: b2f7c9d41a03
Create Date: 2026-08-03

팀 통합 과정에서 회원가입 마이그레이션이 두 갈래로 갈렸다 — 우리 b2f7c9d41a03 과
팀원의 7a2daa32345d → d62d1592c2e9 → 401bffebdcd9 (+ 빈 merge 리비전 bb211b54d478).
두 갈래가 같은 테이블·컬럼을 만들어서, 합친 채로 빈 DB 에 걸면 중복 생성으로 실패한다.

2026-08-03 실측으로 확인한 것:
  - Neon 의 alembic_version 은 b2f7c9d41a03 하나뿐이다
  - departments.code 가 IT_ADMIN 계열이고 employees.department 컬럼이 살아 있다
    → 팀원 리비전 3 개는 어느 DB 에서도 실행된 적이 없다
  - 그럼에도 팀원의 회원가입 코드는 이 스키마 위에서 정상 동작했다
    (SU- 접두사 발급, PENDING_APPROVAL → ACTIVE 승인, REJECTED 거절, email 로그인)

그래서 팀원 리비전 파일을 제거하고 우리 것을 정본으로 삼되, 팀원 안에만 있던
차이를 이 리비전이 채운다. 최종 스키마는 팀원이 의도한 것과 같아진다.

이 리비전이 하는 일:
  1. employees.department (VARCHAR) 를 department_id 로 백필한 뒤 제거
  2. employees.email 을 채우고 NOT NULL 로 전환 (로그인 식별자 확정)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "c8e3a1f75b20"
down_revision: str | None = "b2f7c9d41a03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "service"


def upgrade() -> None:
    # -----------------------------------------------------------------
    # 1. department (VARCHAR) → department_id 백필
    #
    # b2f7c9d41a03 은 department_id 컬럼과 FK 만 만들고 값은 채우지 않았다.
    # 부서명이 정확히 일치하는 행만 연결한다. 일치하지 않는 값이 있는지는
    # 적용 전에 별도 쿼리로 확인했다.
    # -----------------------------------------------------------------
    op.execute(f"""
        UPDATE {SCHEMA}.employees e
           SET department_id = d.id
          FROM {SCHEMA}.departments d
         WHERE e.department_id IS NULL
           AND btrim(e.department) = d.name;
    """)

    # -----------------------------------------------------------------
    # 2. department 컬럼 제거
    #
    # develop 의 Employee 모델에는 이미 이 컬럼 매핑이 없다. 남겨두면
    # 아무도 읽지 않는 컬럼이 되고, 부서명이 두 곳에 존재해 어긋날 수 있다.
    # -----------------------------------------------------------------
    op.drop_column("employees", "department", schema=SCHEMA)

    # -----------------------------------------------------------------
    # 3. email 백필
    #
    # 로그인 식별자가 employee_code 에서 email 로 바뀌었는데 기존 계정에는
    # 이메일이 없다. NOT NULL 을 걸려면 값이 있어야 하므로 placeholder 로
    # 채운다. .invalid 는 예약 TLD 라 실제로 메일이 나가지 않고, 이 주소로는
    # 로그인할 수 없다 — 즉 지금과 동일하게 로그인 불가 상태가 유지된다.
    #
    # 부트스트랩 관리자(DEMO-ADMIN-001)는 앱이 뜰 때 app/core/bootstrap.py 가
    # BOOTSTRAP_ADMIN_EMAIL 로 되돌린다. 데모 계정들은 관리자가 실제 이메일로
    # 갱신하거나 삭제해야 한다.
    #
    # employee_code 가 UNIQUE 라 여기서 파생된 이메일도 UNIQUE 가 보장된다.
    # -----------------------------------------------------------------
    op.execute(f"""
        UPDATE {SCHEMA}.employees
           SET email = lower(employee_code) || '@migrated.invalid'
         WHERE email IS NULL;
    """)

    # -----------------------------------------------------------------
    # 4. email 을 로그인 키로 확정
    #
    # UNIQUE 제약(uq_employees_email)은 b2f7c9d41a03 에서 이미 만들었으므로
    # 여기서는 NOT NULL 만 건다.
    # -----------------------------------------------------------------
    op.alter_column("employees", "email", nullable=False, schema=SCHEMA)


def downgrade() -> None:
    op.alter_column("employees", "email", nullable=True, schema=SCHEMA)

    # 마이그레이션이 만든 placeholder 만 되돌린다. 사람이 넣은 실제 이메일은
    # 건드리지 않는다.
    op.execute(f"""
        UPDATE {SCHEMA}.employees
           SET email = NULL
         WHERE email LIKE '%@migrated.invalid';
    """)

    op.add_column(
        "employees",
        sa.Column("department", sa.String(length=100), nullable=True),
        schema=SCHEMA,
    )
    op.execute(f"""
        UPDATE {SCHEMA}.employees e
           SET department = d.name
          FROM {SCHEMA}.departments d
         WHERE e.department_id = d.id;
    """)
