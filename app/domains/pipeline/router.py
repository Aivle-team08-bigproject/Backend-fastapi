import asyncio

from fastapi import APIRouter, Depends, status
from fastapi.responses import FileResponse, StreamingResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import not_found
from app.common.security_deps import CurrentAuth, get_current_auth
from app.core.config import settings
from app.db.session import get_db
from app.domains.pipeline.model import PipelineRun
from app.domains.pipeline.schema import (
    CreateDataRequestRequest,
    CreateDataRequestResponse,
    PipelineRunResponse,
    ProcessingResultResponse,
    SamplePreviewResponse,
    StageReviewRequest,
    StageReviewResponse,
)
from app.domains.pipeline.service import (
    create_data_request,
    get_pipeline_run,
    get_processing_result,
    get_result_artifact,
    get_sample_preview,
    submit_stage_review,
)
from app.worker.file_storage import resolve_storage_key
from app.worker.status_event import PipelineStatusEvent

router = APIRouter(prefix="/api/v1", tags=["pipeline"])


@router.post(
    "/data-requests",
    response_model=CreateDataRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_request(
    payload: CreateDataRequestRequest,
    auth: CurrentAuth = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> CreateDataRequestResponse:
    return await create_data_request(db, payload, auth.employee)


@router.get("/runs/{run_id}", response_model=PipelineRunResponse)
async def get_run(
    run_id: int,
    db: AsyncSession = Depends(get_db),
) -> PipelineRunResponse:
    return await get_pipeline_run(db, run_id)


@router.get(
    "/runs/{run_id}/sample-preview",
    response_model=SamplePreviewResponse,
)
async def get_run_sample_preview(
    run_id: int,
    db: AsyncSession = Depends(get_db),
) -> SamplePreviewResponse:
    return await get_sample_preview(db, run_id)


@router.get(
    "/runs/{run_id}/processing-result",
    response_model=ProcessingResultResponse,
)
async def get_run_processing_result(
    run_id: int,
    db: AsyncSession = Depends(get_db),
) -> ProcessingResultResponse:
    return await get_processing_result(db, run_id)


@router.post("/runs/{run_id}/review", response_model=StageReviewResponse)
async def review_run_stage(
    run_id: int,
    payload: StageReviewRequest,
    auth: CurrentAuth = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> StageReviewResponse:
    """단계 산출물 검토(HITL). 승인 시 다음 단계로, 반려 시 해당 단계로 되돌린다."""
    return await submit_stage_review(db, run_id, auth.employee, payload)


@router.get("/runs/{run_id}/result.csv")
async def download_run_result(run_id: int, db: AsyncSession = Depends(get_db)):
    artifact = await get_result_artifact(db, run_id)
    try:
        path = resolve_storage_key(artifact.storage_key)
    except ValueError as exc:
        raise not_found("PIPELINE_RESULT_NOT_FOUND", "결과 파일을 찾을 수 없습니다.") from exc
    if not path.is_file():
        raise not_found("PIPELINE_RESULT_NOT_FOUND", "결과 파일을 찾을 수 없습니다.")
    return FileResponse(
        path,
        media_type=artifact.mime_type or "text/csv",
        filename=f"pipeline-run-{run_id}-result.csv",
    )


def _sse_message(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


@router.get("/runs/{run_id}/events")
async def stream_run_events(run_id: int, db: AsyncSession = Depends(get_db)):
    run = await db.get(PipelineRun, run_id)
    if run is None:
        raise not_found("PIPELINE_RUN_NOT_FOUND", "파이프라인 실행을 찾을 수 없습니다.")
    database_initial = PipelineStatusEvent(
        run_id=run.id,
        celery_task_id=run.celery_task_id or "not-dispatched",
        run_status=run.status,
        current_stage=run.current_stage,
        progress_percent=run.progress_percent,
        message="현재 파이프라인 상태입니다.",
        occurred_at=run.updated_at,
    ).model_dump_json()

    async def event_stream():
        redis_client = Redis.from_url(settings.worker_status_redis_url, decode_responses=True)
        pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe(settings.worker_status_sse_channel)
        try:
            latest_key = f"{settings.worker_status_key_prefix}:{run_id}"
            latest = await redis_client.get(latest_key)
            yield _sse_message("status", latest or database_initial)
            while True:
                message = await pubsub.get_message(timeout=15)
                if message is None:
                    yield ": heartbeat\n\n"
                    continue
                event = PipelineStatusEvent.model_validate_json(message["data"])
                if event.run_id == run_id:
                    yield _sse_message("status", event.model_dump_json())
        except asyncio.CancelledError:
            raise
        finally:
            await pubsub.unsubscribe(settings.worker_status_sse_channel)
            await pubsub.aclose()
            await redis_client.aclose()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
