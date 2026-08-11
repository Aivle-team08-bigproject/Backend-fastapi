import os
import asyncio

import pytest

# app.core.config.settings는 모듈 임포트 시점에 딱 한 번 생성되는 싱글턴이라,
# 앱 코드를 import하기 전에 환경변수를 먼저 세팅해야 한다.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://appuser:change_me_strong_password@127.0.0.1:5432/appdb",
)
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-at-least-32-bytes-long-2026")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "TestAdmin!2026Secure")
os.environ.setdefault("BOOTSTRAP_ADMIN_ID", "HANA-ADMIN-001")
os.environ.setdefault("BOOTSTRAP_ADMIN_EMAIL", "admin@company.com")
os.environ.setdefault("ALLOWED_EMAIL_DOMAINS", "company.com")

BOOTSTRAP_ADMIN_PASSWORD = os.environ["BOOTSTRAP_ADMIN_PASSWORD"]
BOOTSTRAP_ADMIN_ID = os.environ["BOOTSTRAP_ADMIN_ID"]
BOOTSTRAP_ADMIN_EMAIL = os.environ["BOOTSTRAP_ADMIN_EMAIL"]


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


def department_id(client, name: str = "데이터사업팀") -> int:
    """마이그레이션이 심어둔 기본 부서 목록에서 이름으로 id를 찾는다 (테스트 전용 헬퍼)."""
    response = client.get("/api/public/departments")
    assert response.status_code == 200, response.text
    for item in response.json():
        if item["name"] == name:
            return item["id"]
    raise AssertionError(f"'{name}' 부서를 찾을 수 없습니다 (마이그레이션 기본 부서 목록 확인 필요)")


@pytest.fixture()
def dashboard_factory():
    """Unique workflow fixture factory for dashboard contract tests.

    테스트가 끝나면 만든 행을 되돌린다 — 대시보드 응답이 테이블 전체 집계의 상위 5개만
    돌려주기 때문에, 남은 행이 쌓이면 다음 실행에서 새 데이터가 상위권에 못 들어간다.
    """
    from tests.dashboard_fixtures import DashboardFixtureFactory

    factory = DashboardFixtureFactory()
    try:
        yield factory
    finally:
        factory.cleanup()
