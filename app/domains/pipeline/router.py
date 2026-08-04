import asyncio
import json

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import FileResponse, StreamingResponse
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import not_found
from app.common.security_deps import CurrentAuth, get_current_auth
from app.core.config import settings
from app.db.session import get_db
from app.domains.pipeline.model import PipelineEvent, PipelineRun, StageRun
from app.domains.pipeline.selection_steps import (
    SELECTION_STEP_ORDER,
    initial_selection_steps_snapshot,
)
from app.domains.pipeline.processing_steps import (
    PROCESSING_STEP_ORDER,
    initial_processing_steps_snapshot,
)
from app.domains.pipeline.schema import (
    CreateDataRequestRequest,
    CreateDataRequestResponse,
    PipelineRunResponse,
    SamplePreviewResponse,
    StageReviewRequest,
    StageReviewResponse,
)
from app.domains.pipeline.service import (
    create_data_request,
    get_pipeline_run,
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
    db: AsyncSession = Depends(get_db),
) -> CreateDataRequestResponse:
    return await create_data_request(db, payload)


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


def _sse_message(event: str, data: str, event_id: int | None = None) -> str:
    id_line = f"id: {event_id}\n" if event_id is not None else ""
    return f"{id_line}event: {event}\ndata: {data}\n\n"


def _stored_event_payload(event: PipelineEvent) -> str:
    """DB 이력 행을 Redis 실시간 이벤트와 같은 공개 형태로 직렬화한다."""
    payload = event.payload or {}
    return json.dumps(
        {
            "event_id": event.id,
            "run_id": event.pipeline_run_id,
            "run_status": payload.get("status"),
            "current_stage": payload.get("stage"),
            "stage_run_id": event.stage_run_id,
            "selection_step": payload.get("selection_step"),
            "selection_step_status": payload.get("selection_step_status"),
            "processing_step": payload.get("processing_step"),
            "processing_step_status": payload.get("processing_step_status"),
            "attempt_no": payload.get("attempt_no"),
            "progress_percent": payload.get("progress_percent"),
            "message": event.message,
            "step_metadata": payload.get("step_metadata"),
            "occurred_at": event.occurred_at.isoformat(),
        },
        ensure_ascii=False,
    )


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: int,
    _auth: CurrentAuth = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
    last_event_id: int | None = Header(default=None, alias="Last-Event-ID", ge=1),
):
    """로그인한 직원에게 파이프라인 상태를 SSE로 전달한다.

    기본 EventSource는 Authorization 헤더를 지정할 수 없으므로 프론트엔드는
    기존 access token을 사용하는 fetch 기반 SSE 클라이언트로 연결한다.
    """
    run = await db.get(PipelineRun, run_id)
    if run is None:
        raise not_found("PIPELINE_RUN_NOT_FOUND", "파이프라인 실행을 찾을 수 없습니다.")

    async def event_stream():
        redis_client = Redis.from_url(settings.worker_status_redis_url, decode_responses=True)
        pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
        # DB 조회 중 발생하는 이벤트도 Redis 큐에 들어오도록 구독을 먼저 연다.
        await pubsub.subscribe(settings.worker_status_sse_channel)
        try:
            boundary_id = await db.scalar(
                select(func.max(PipelineEvent.id)).where(
                    PipelineEvent.pipeline_run_id == run_id
                )
            )
            boundary_id = int(boundary_id or 0)

            missed_frames: list[str] = []
            if last_event_id is not None and last_event_id < boundary_id:
                missed_events = list(
                    (
                        await db.scalars(
                            select(PipelineEvent)
                            .where(
                                PipelineEvent.pipeline_run_id == run_id,
                                PipelineEvent.id > last_event_id,
                                PipelineEvent.id <= boundary_id,
                            )
                            .order_by(PipelineEvent.id)
                        )
                    ).all()
                )
                for stored_event in missed_events:
                    missed_frames.append(
                        _sse_message(
                            "status",
                            _stored_event_payload(stored_event),
                            stored_event.id,
                        )
                    )

            # 최초 run 조회 이후 상태 변경이 있었을 수 있으므로 snapshot 직전에 갱신한다.
            await db.refresh(run)
            stage = None
            if run.current_stage:
                stage = await db.scalar(
                    select(StageRun)
                    .where(
                        StageRun.pipeline_run_id == run_id,
                        StageRun.stage_code == run.current_stage,
                    )
                    .order_by(StageRun.attempt_no.desc(), StageRun.id.desc())
                    .limit(1)
                )
            items = []
            if stage and stage.stage_code == "DATA_SELECTION":
                steps = (stage.output_payload or {}).get("selection_steps")
                if not isinstance(steps, dict):
                    steps = initial_selection_steps_snapshot()
                initial_steps = initial_selection_steps_snapshot()
                items = [
                    {
                        "item_code": code.value,
                        **(
                            steps.get(code.value)
                            if isinstance(steps.get(code.value), dict)
                            else initial_steps[code.value]
                        ),
                    }
                    for code in SELECTION_STEP_ORDER
                ]
            elif stage and stage.stage_code == "DATA_PROCESSING":
                steps = (stage.output_payload or {}).get("processing_steps")
                if not isinstance(steps, dict):
                    steps = initial_processing_steps_snapshot()
                initial_steps = initial_processing_steps_snapshot()
                items = [
                    {
                        "item_code": code.value,
                        **(
                            steps.get(code.value)
                            if isinstance(steps.get(code.value), dict)
                            else initial_steps[code.value]
                        ),
                    }
                    for code in PROCESSING_STEP_ORDER
                ]
            snapshot = json.dumps(
                {
                    "event_id": boundary_id or None,
                    "run_id": run.id,
                    "run_status": run.status,
                    "current_stage": run.current_stage,
                    "progress_percent": run.progress_percent,
                    "stage_run_id": stage.id if stage else None,
                    "attempt_no": stage.attempt_no if stage else None,
                    "items": items,
                    "message": "현재 파이프라인 상태입니다.",
                    "occurred_at": run.updated_at.isoformat(),
                },
                ensure_ascii=False,
                default=str,
            )
            # 장시간 SSE 연결 동안 DB 트랜잭션과 커넥션을 잡고 있지 않는다.
            await db.rollback()
            for frame in missed_frames:
                yield frame
            yield _sse_message("snapshot", snapshot, boundary_id or None)

            last_sent_id = boundary_id
            while True:
                message = await pubsub.get_message(timeout=15)
                if message is None:
                    yield ": heartbeat\n\n"
                    continue
                event = PipelineStatusEvent.model_validate_json(message["data"])
                if event.run_id != run_id:
                    continue
                if event.event_id is not None and event.event_id <= last_sent_id:
                    continue
                if event.event_id is not None:
                    last_sent_id = event.event_id
                yield _sse_message(
                    "status", event.model_dump_json(), event.event_id
                )
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
