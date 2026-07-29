import asyncio
import sys

if sys.platform == "win32":
    # psycopg의 async 모드는 Windows 기본 ProactorEventLoop를 못 쓴다. 아래
    # app.api.router import가 끌고 오는 의존성(strands-agents 등) 중 하나가 이
    # 정책을 다시 Proactor로 덮어쓰므로, 다른 모든 import보다 먼저 설정해야 한다.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.queue import create_redis_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 테이블 정의는 sqlfiles/migrations/V009가 소유한다.
    # create_all/init_db를 쓰지 않는다 — 앱이 스키마를 만들지 않는다.
    app.state.redis = await create_redis_pool()
    try:
        yield
    finally:
        await app.state.redis.aclose()

app = FastAPI(
    title="Automation Supervisor API",
    version="1.0.0",
    description="Supervisor that controls requirement analysis, data selection, data processing, QA, caching, and rollback.",
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
async def health():
    return {"status": "ok"}
