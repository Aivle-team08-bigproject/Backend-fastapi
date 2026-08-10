from contextlib import asynccontextmanager
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.common.session_activity_middleware import SessionActivityMiddleware
from app.api.router import api_router
from app.core.config import settings
from app.domains.pipeline.email_result_worker import consume_email_result_queue


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = asyncio.Event()
    result_task = asyncio.create_task(consume_email_result_queue(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        result_task.cancel()
        await asyncio.gather(result_task, return_exceptions=True)


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
