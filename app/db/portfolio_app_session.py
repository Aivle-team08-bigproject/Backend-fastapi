"""portfolio DB, app_svc 계정 전용 비동기 SQLAlchemy 세션.

service 스키마 전체 + mart 스키마(담당자 대시보드용)에 사용한다.
agent_svc 세션(app/db/portfolio_agent_session.py)과 엔진을 공유하지 않는다.

Base: 이 엔진으로 매핑할 ORM 모델(app/domains/requirements 등)의 메타데이터.
테이블은 이미 sqlfiles/V7 DDL로 존재하므로 create_all()은 쓰지 않는다
(app/db/session.py의 init_db()와 달리, 여기선 마이그레이션 주체가 SQL 파일이다).
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.runtime_database_url(settings.portfolio_app_database_url),
    echo=False,
    future=True,
    pool_pre_ping=True,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db_app() -> AsyncGenerator[AsyncSession, None]:
    """요청 하나당 세션 하나. app_svc 계정으로 접속된 세션을 반환한다."""
    async with AsyncSessionLocal() as session:
        yield session
