"""requirements/tasks 도메인 전용 동기 SQLAlchemy 세션.

employee/auth/session 도메인은 app/db/session.py의 비동기 엔진(asyncpg)을 쓰고,
requirements/tasks 도메인은 별도 동기 엔진(psycopg2) + 별도 Base(메타데이터)를 쓴다.
두 도메인은 서로 다른 데이터베이스/모델 집합이므로 엔진을 공유하지 않는다.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.requirements_database_url, pool_pre_ping=True)
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
