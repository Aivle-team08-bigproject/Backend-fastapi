from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.common.session_activity_middleware import SessionActivityMiddleware
from app.api.router import api_router
from app.core.bootstrap import ensure_bootstrap_admin
from app.core.config import settings
from app.db.session import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()  # 로컬 개발용 테이블 자동 생성 (운영은 Alembic 등 마이그레이션 권장)
    await ensure_bootstrap_admin()
    yield


app = FastAPI(title="hana-data-market agent-service", lifespan=lifespan)

app.add_middleware(SessionActivityMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With", "X-User-Activity"],
)

app.include_router(api_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
