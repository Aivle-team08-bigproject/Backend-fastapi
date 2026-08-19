from contextlib import asynccontextmanager
import asyncio

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy import text

from app.common.session_activity_middleware import SessionActivityMiddleware
from app.api.router import api_router
from app.core.config import settings
from app.db.session import engine
from app.domains.pipeline.email_result_worker import consume_email_result_queue
from app.domains.pipeline.email_retry_worker import monitor_stale_email_deliveries
from app.domains.pipeline.stale_run_monitor import monitor_stale_pipeline_runs


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = asyncio.Event()
    result_task = asyncio.create_task(consume_email_result_queue(stop_event))
    retry_task = asyncio.create_task(monitor_stale_email_deliveries(stop_event))
    stale_run_task = asyncio.create_task(monitor_stale_pipeline_runs(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        result_task.cancel()
        retry_task.cancel()
        stale_run_task.cancel()
        await asyncio.gather(result_task, retry_task, stale_run_task, return_exceptions=True)


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


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready() -> JSONResponse:
    """Return readiness only when the dependencies needed to serve requests work."""
    checks: dict[str, str] = {}
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"

    redis_client = Redis.from_url(settings.worker_status_redis_url, socket_connect_timeout=2)
    try:
        await redis_client.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"
    finally:
        await redis_client.aclose()

    ready = all(value == "ok" for value in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ok" if ready else "unavailable", "checks": checks},
    )


@app.get("/health")
async def health() -> dict[str, str]:
    """Backward-compatible alias for the liveness probe."""
    return await health_live()
