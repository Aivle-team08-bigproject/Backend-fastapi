from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
# 도메인 모델을 import해 metadata에 등록한다.
from app.domains.employees import model as employee_model  # noqa: F401
from app.domains.auth.model import session_model  # noqa: F401
from app.domains.automation.model import requirements_analysis_model  # noqa: F401
from app.domains.pipeline import model as pipeline_model  # noqa: F401
from app.domains.dashboard import model as dashboard_model  # noqa: F401

engine = create_async_engine(
    settings.runtime_database_url(settings.portfolio_app_database_url),
    echo=False,
    future=True,
    pool_pre_ping=True,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """요청 하나당 세션 하나. FastAPI가 요청 범위로 캐싱해준다."""
    async with AsyncSessionLocal() as session:
        yield session
