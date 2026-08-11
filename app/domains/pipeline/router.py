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
from app.db.session import AsyncSessionLocal, get_db
from app.domains.pipeline.model import DataRequest, EventType, PipelineEvent, PipelineRun, StageRun
from app.domains.pipeline.failure import public_failure
from app.domains.pipeline.analysis_steps import (
    ANALYSIS_STEP_ORDER,
    initial_analysis_steps_snapshot,
)
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
    CreateEmailDeliveryRequest,
    EmailDeliveryResponse,
    PipelineRunResponse,
    ProcessingResultResponse,
    SamplePreviewResponse,
    StageReviewRequest,
    StageReviewResponse,
)
from app.domains.pipeline.service import (
    create_data_request,
    create_email_delivery,
    get_pipeline_run,
    get_processing_result,
    get_result_artifact,
    get_sample_preview,
    result_download_filename,
    submit_stage_review,
)
from app.domains.pipeline.email_queue import EmailDeliveryQueueMessage, publish_email_delivery
from app.worker.file_storage import resolve_storage_key
from app.worker.status_event import PipelineStatusEvent

router = APIRouter(prefix="/api/v1", tags=["pipeline"])

# 최초 SSE 접속 시 되살려 보낼 기술 로그 개수. 프론트 로그 패널이 보관하는 줄 수와 같은
# 수준으로 맞춰 두어 화면을 넘치게 하지 않는다.
AGENT_LOG_REPLAY_LIMIT = 200


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
    auth: CurrentAuth = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SamplePreviewResponse:
    return await get_sample_preview(db, run_id, auth.employee, auth.permissions)


@router.post(
    "/runs/{run_id}/email-deliveries",
    response_model=EmailDeliveryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_email_delivery(
    run_id: int,
    payload: CreateEmailDeliveryRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth: CurrentAuth = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> EmailDeliveryResponse:
    delivery = await create_email_delivery(
        db,
        run_id,
        payload,
        idempotency_key or "",
        auth.employee,
        auth.permissions,
    )
    # FastAPI가 인증·검증·상태 저장을 끝낸 뒤 큐에 발행한다. 큐가 비활성화된
    # 로컬 환경에서는 QUEUED 상태로 남겨 두어 운영 전환 시 재처리할 수 있다.
    preview = await get_sample_preview(db, run_id, auth.employee, auth.permissions)
    await publish_email_delivery(
        EmailDeliveryQueueMessage(
            delivery_id=delivery.delivery_id,
            idempotency_key=(idempotency_key or "").strip(),
            run_id=delivery.run_id,
            stage_attempt_no=delivery.stage_attempt_no,
            delivery_type=delivery.delivery_type,
            recipient=delivery.recipient,
            template_version=delivery.template_version,
            sample_columns=[column.model_dump() for column in preview.columns],
            sample_rows=preview.rows,
            sample_metadata=preview.metadata,
            sample_sha256=delivery.sample_sha256,
        )
    )
    return delivery


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
    request_no = await db.scalar(
        select(DataRequest.request_no)
        .join(PipelineRun, PipelineRun.data_request_id == DataRequest.id)
        .where(PipelineRun.id == run_id)
    )
    if request_no is None:
        raise not_found("PIPELINE_RUN_NOT_FOUND", "파이프라인 실행을 찾을 수 없습니다.")
    try:
        path = resolve_storage_key(artifact.storage_key)
    except ValueError as exc:
        raise not_found("PIPELINE_RESULT_NOT_FOUND", "결과 파일을 찾을 수 없습니다.") from exc
    if not path.is_file():
        raise not_found("PIPELINE_RESULT_NOT_FOUND", "결과 파일을 찾을 수 없습니다.")
    return FileResponse(
        path,
        media_type=artifact.mime_type or "text/csv",
        filename=result_download_filename(request_no, run_id),
    )


def _sse_message(event: str, data: str, event_id: int | None = None) -> str:
    id_line = f"id: {event_id}\n" if event_id is not None else ""
    return f"{id_line}event: {event}\ndata: {data}\n\n"


def _stored_event_payload(event: PipelineEvent) -> str:
    """DB 이력 행을 Redis 실시간 이벤트와 같은 공개 형태로 직렬화한다."""
    payload = event.payload or {}
    is_agent_log = event.event_type == EventType.AGENT_LOG.value
    return json.dumps(
        {
            "event_id": event.id,
            "run_id": event.pipeline_run_id,
            "event_kind": "agent_log" if is_agent_log else "status",
            "log_level": payload.get("log_level") if is_agent_log else None,
            "run_status": payload.get("status"),
            "current_stage": payload.get("stage"),
            "stage_run_id": event.stage_run_id,
            "analysis_step": payload.get("analysis_step"),
            "analysis_step_status": payload.get("analysis_step_status"),
            "selection_step": payload.get("selection_step"),
            "selection_step_status": payload.get("selection_step_status"),
            "processing_step": payload.get("processing_step"),
            "processing_step_status": payload.get("processing_step_status"),
            "attempt_no": payload.get("attempt_no"),
            "progress_percent": payload.get("progress_percent"),
            "message": event.message,
            # agent_log는 step_metadata 대신 detail에 기술 정보를 담는다.
            "step_metadata": payload.get("detail") if is_agent_log else payload.get("step_metadata"),
            "failure": payload.get("failure"),
            "rollback_to_stage": payload.get("rollback_to_stage"),
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
        # FastAPI가 주입하는 db 세션은 핸들러가 StreamingResponse 객체를 반환하는 즉시
        # 정리된다(스트림 바디를 실제로 소비하기 전에 종료) — 여기서 그 db를 계속 쓰면
        # 첫 쿼리 이후 오브젝트가 세션에서 떨어져나가 곧바로 끊긴다. 그래서 스트림 생존
        # 기간 전체를 커버하는 세션을 따로 연다.
        async with AsyncSessionLocal() as stream_db:
            redis_client = Redis.from_url(settings.worker_status_redis_url, decode_responses=True)
            pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
            # DB 조회 중 발생하는 이벤트도 Redis 큐에 들어오도록 구독을 먼저 연다.
            await pubsub.subscribe(settings.worker_status_sse_channel)
            try:
                boundary_id = await stream_db.scalar(
                    select(func.max(PipelineEvent.id)).where(
                        PipelineEvent.pipeline_run_id == run_id
                    )
                )
                boundary_id = int(boundary_id or 0)

                missed_frames: list[str] = []
                if last_event_id is not None and last_event_id < boundary_id:
                    missed_events = list(
                        (
                            await stream_db.scalars(
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
                elif last_event_id is None:
                    # 최초 접속(새로고침·뒤늦은 진입)에는 snapshot이 현재 단계 상태만 담고
                    # 지나간 로그는 담지 못한다. 실패 원인을 되짚으려면 그 로그가 필요하므로
                    # 최근 기술 로그를 시간순으로 먼저 흘려보내 화면을 복원한다.
                    recent_logs = list(
                        (
                            await stream_db.scalars(
                                select(PipelineEvent)
                                .where(
                                    PipelineEvent.pipeline_run_id == run_id,
                                    PipelineEvent.event_type == EventType.AGENT_LOG.value,
                                )
                                .order_by(PipelineEvent.id.desc())
                                .limit(AGENT_LOG_REPLAY_LIMIT)
                            )
                        ).all()
                    )
                    for stored_event in reversed(recent_logs):
                        missed_frames.append(
                            _sse_message(
                                "status",
                                _stored_event_payload(stored_event),
                                # snapshot이 뒤이어 boundary_id로 커서를 잡으므로 여기서는
                                # Last-Event-ID를 앞당기지 않는다.
                                None,
                            )
                        )

                # 별도 세션이라 최초 존재 확인 이후 상태 변경분까지 반영하도록 다시 조회한다.
                run = await stream_db.get(PipelineRun, run_id)
                stage = None
                if run.current_stage:
                    stage = await stream_db.scalar(
                        select(StageRun)
                        .where(
                            StageRun.pipeline_run_id == run_id,
                            StageRun.stage_code == run.current_stage,
                        )
                        .order_by(StageRun.attempt_no.desc(), StageRun.id.desc())
                        .limit(1)
                    )
                items = []
                if stage and stage.stage_code == "REQUIREMENT_ANALYSIS":
                    steps = (stage.output_payload or {}).get("analysis_steps")
                    if not isinstance(steps, dict):
                        steps = initial_analysis_steps_snapshot()
                    initial_steps = initial_analysis_steps_snapshot()
                    items = [
                        {
                            "item_code": code.value,
                            **(
                                steps.get(code.value)
                                if isinstance(steps.get(code.value), dict)
                                else initial_steps[code.value]
                            ),
                        }
                        for code in ANALYSIS_STEP_ORDER
                    ]
                elif stage and stage.stage_code == "DATA_SELECTION":
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
                        "error_message": run.error_message,
                        "failure": public_failure(
                            stage=stage.stage_code if stage else run.current_stage,
                            result=stage.output_payload if stage else None,
                            validation_result=stage.validation_result if stage else None,
                            rollback_to_stage=run.rollback_to_stage,
                            error_message=(stage.error_message if stage else None) or run.error_message,
                        ),
                        "occurred_at": run.updated_at.isoformat(),
                    },
                    ensure_ascii=False,
                    default=str,
                )
                # 장시간 SSE 연결 동안 DB 트랜잭션과 커넥션을 잡고 있지 않는다.
                await stream_db.rollback()
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
