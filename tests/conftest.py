import os
import asyncio

import pytest

# app.core.config.settings는 모듈 임포트 시점에 딱 한 번 생성되는 싱글턴이라,
# 앱 코드를 import하기 전에 환경변수를 먼저 세팅해야 한다.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://appuser:change_me_strong_password@127.0.0.1:5432/appdb",
)
os.environ.setdefault(
    "REQUIREMENTS_DATABASE_URL",
    "postgresql+psycopg://appuser:change_me_strong_password@127.0.0.1:5432/appdb",
)
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-at-least-32-bytes-long-2026")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "TestAdmin!2026Secure")
os.environ.setdefault("BOOTSTRAP_ADMIN_ID", "HANA-ADMIN-001")

BOOTSTRAP_ADMIN_PASSWORD = os.environ["BOOTSTRAP_ADMIN_PASSWORD"]
BOOTSTRAP_ADMIN_ID = os.environ["BOOTSTRAP_ADMIN_ID"]


@pytest.fixture(scope="session", autouse=True)
def _clean_test_database():
    yield
    from app.db.session import engine

    asyncio.run(engine.dispose())


@pytest.fixture()
def client():
    """테스트들이 하나의 Postgres 테스트 DB를 공유한다.
    테스트마다 독립적인 상태가 필요하면 employee_code를 유니크하게 만들어서 격리하는 걸 권장.
    """
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def settings():
    from app.core.config import settings as app_settings

    return app_settings
