"""portfolio DB, agent_svc 계정 전용 비동기 SQLAlchemy 세션.

anon 스키마 읽기 + service 스키마 중 파이프라인 로그성 테이블(읽기/쓰기)에 사용한다.
app_svc 세션(app/db/portfolio_app_session.py)과 엔진을 공유하지 않는다 —
계정 경계가 코드 버그와 무관하게 유지되도록 커넥션 자체를 분리한다.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    settings.portfolio_agent_database_url, echo=False, future=True, pool_pre_ping=True
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db_agent() -> AsyncGenerator[AsyncSession, None]:
    """요청 하나당 세션 하나. agent_svc 계정으로 접속된 세션을 반환한다."""
    async with AsyncSessionLocal() as session:
        yield session