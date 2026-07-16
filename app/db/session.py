

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.base import Base

# 도메인 모델을 전부 import해둬야 create_all() 시 테이블이 실제로 생성된다.
from app.domains.employees.model import employee_model  # noqa: F401
from app.domains.employees.model import audit_log_model  # noqa: F401
from app.domains.auth.model import session_model  # noqa: F401

engine = create_async_engine(settings.database_url, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """요청 하나당 세션 하나. FastAPI가 요청 범위로 캐싱해준다."""
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """로컬 개발용: 테이블이 없으면 생성한다. 운영에서는 Alembic 등 마이그레이션 도구 사용을 권장."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app.models import requirement, task  # noqa: F401
    Base.metadata.create_all(bind=engine)
<
