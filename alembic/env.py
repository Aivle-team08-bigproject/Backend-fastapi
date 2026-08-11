from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

from app.db.base import Base
# 아래 import들은 Base.metadata에 테이블을 등록시키기 위한 것 — 실제로 안 써도 import 자체가 필요
from app.domains.employees import model as employee_model  # noqa: F401
from app.domains.auth.model import session_model  # noqa: F401
from app.domains.pipeline import model as pipeline_model  # noqa: F401
from app.domains.dashboard import model as dashboard_model  # noqa: F401
# 🔴 아래 둘이 빠져 있어 `alembic check` 가 notices·pii_detections 를 DROP 하자고
#    제안했다. 모델 파일은 있는데 metadata 에 등록이 안 된 상태였다.
from app.domains.notices import model as notice_model  # noqa: F401
from app.common.pii import model as pii_model  # noqa: F401

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

import os
import sys
sys.path.insert(0, os.getcwd())  # app 패키지를 import할 수 있게 경로 추가

from app.core.config import settings
config.set_main_option(
    "sqlalchemy.url",
    settings.portfolio_migration_database_url,
)


# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata


def include_object(object, name, type_, reflected, compare_to):
    """autogenerate 대상을 service 스키마로 한정한다.

    왜 필요한가 — alembic 은 기본적으로 연결의 기본 스키마(public)만 읽는다.
    우리 모델은 전부 schema="service" 라 DB 쪽 service 스키마를 안 읽고,
    그 결과 `alembic check` 가 **28개 테이블 전부를 add_table 로 제안**했다.
    드리프트 감지 도구로 쓸 수 없고, 누군가 `revision --autogenerate` 를 돌리면
    이미 있는 테이블을 다시 만드는 마이그레이션이 나온다.

    그래서 include_schemas=True 를 켜는데, 그것만 켜면 이번엔 mart·anonymized
    11개 테이블에 대해 **drop_table 을 제안한다** — 모델에 없기 때문이다.
    두 스키마는 sqlfiles-정본 이 관리하는 영역이라 alembic 이 건드리면 안 된다.
    이 필터가 그 짝이다. 둘은 항상 함께 간다.
    """
    if type_ == "table":
        return object.schema == "service"
    # 인덱스·제약·컬럼은 소속 테이블의 스키마를 따른다
    parent = getattr(object, "table", None)
    return parent is None or parent.schema == "service"

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema="service",
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema="service",
            include_schemas=True,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
