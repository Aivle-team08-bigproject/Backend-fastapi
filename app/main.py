from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


from app.common.session_activity_middleware import SessionActivityMiddleware
from app.api.router import api_router
from app.core.bootstrap import ensure_bootstrap_admin

from app.api.router import api_router

from app.core.config import settings
from app.db.session import init_db


@asynccontextmanager

async def lifespan(app: FastAPI):
    await init_db()  # 로컬 개발용 테이블 자동 생성 (운영은 Alembic 등 마이그레이션 권장)
    await ensure_bootstrap_admin()
    yield


app = FastAPI(title="portfolio-data-market agent-service", lifespan=lifespan)

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

async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="요구사항 CRUD, 상태 관리, 작업 관리를 제공하는 FastAPI 백엔드",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/health", tags=["System"])
def health():

    return {"status": "ok"}
