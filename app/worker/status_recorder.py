"""작업 상태 기록 — Worker가 DB에 직접 쓰고, 그 다음 프론트 화면 갱신용으로 발행한다.

쓰기 주체는 Worker다. Redis pub/sub은 영속화 경로가 아니라 화면 갱신 전용 통로다:

    Worker --(1) DB write--> PostgreSQL
           --(2) publish---> Redis --> FastAPI SSE --> 프론트 화면

(2)가 실패해도 상태는 이미 (1)에서 남아 있으므로 유실되지 않는다. 반대로 (1)이 실패하면
발행도 하지 않는다 — 화면에 DB에 없는 상태가 보이는 상황을 안 만든다.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.time_utils import utcnow
from app.domains.pipeline.model import (
    Artifact,
    ArtifactType,
    DataRequest,
    DataRequestStatus,
    EventType,
    PipelineEvent,
    PipelineRun,
    PipelineRunStatus,
    PiiScanStatus,
    StageRun,
    StageRunStatus,
)
from app.worker.status_event import PipelineStatusEvent
from app.worker.status_publisher import publish_to_screen


logger = logging.getLogger(__name__)


async def persist_status_event(db: AsyncSession, event: PipelineStatusEvent) -> bool:
    run = await db.get(PipelineRun, event.run_id)
    if run is None or run.celery_task_id != event.celery_task_id:
        logger.warning("Ignoring unknown or mismatched pipeline event for run_id=%s", event.run_id)
        return False

    now = event.occurred_at
    run.status = event.run_status.value
    run.current_stage = event.current_stage
    run.progress_percent = event.progress_percent
    run.updated_at = now
    if event.run_status == PipelineRunStatus.RUNNING and run.started_at is None:
        run.started_at = now
    if event.run_status in {PipelineRunStatus.COMPLETED, PipelineRunStatus.FAILED}:
        run.completed_at = now
    # Supervisor가 다음 dispatch에서 읽는 값들. 성공 이벤트에서는 지워서 이전 실패 흔적이
    # 남지 않게 한다.
    run.error_message = event.error_message
    run.rollback_to_stage = event.rollback_to_stage

    data_request = await db.get(DataRequest, run.data_request_id)
    if data_request is not None:
        if event.run_status == PipelineRunStatus.FAILED:
            data_request.status = DataRequestStatus.FAILED.value
        elif event.run_status == PipelineRunStatus.COMPLETED:
            data_request.status = DataRequestStatus.COMPLETED.value
        elif event.run_status == PipelineRunStatus.RUNNING:
            data_request.status = DataRequestStatus.RUNNING.value
        else:
            data_request.status = DataRequestStatus.WAITING_REVIEW.value
        data_request.updated_at = now

    stage = None
    if event.current_stage and event.stage_status is not None:
        # 롤백 재시도로 같은 stage_code가 여러 attempt 존재할 수 있으므로 최신 시도를 잡는다.
        stage = await db.scalar(
            select(StageRun)
            .where(
                StageRun.pipeline_run_id == run.id,
                StageRun.stage_code == event.current_stage,
            )
            .order_by(StageRun.attempt_no.desc(), StageRun.id.desc())
            .limit(1)
        )
        if stage is not None:
            stage.status = event.stage_status.value
            stage.executor_reference = event.celery_task_id
            if event.validation_result is not None:
                stage.validation_result = event.validation_result
            if event.stage_status == StageRunStatus.RUNNING:
                stage.started_at = stage.started_at or now
            elif event.stage_status == StageRunStatus.COMPLETED:
                stage.output_payload = event.result or {}
                stage.completed_at = now
            elif event.stage_status == StageRunStatus.FAILED:
                stage.output_payload = event.result or {}
                stage.error_message = event.error_message
                stage.completed_at = now

    db.add(
        PipelineEvent(
            pipeline_run_id=run.id,
            stage_run_id=stage.id if stage else None,
            event_type=EventType.FAILED.value
            if event.run_status == PipelineRunStatus.FAILED
            else EventType.PROGRESS.value,
            severity="ERROR" if event.run_status == PipelineRunStatus.FAILED else "INFO",
            message=event.message,
            payload={
                "stage": event.current_stage,
                "status": event.run_status.value,
                "progress_percent": event.progress_percent,
                "result": event.result,
                "error_message": event.error_message,
            },
            occurred_at=now,
        )
    )
    # HITL 도입 후 가공 단계가 끝나면 run은 COMPLETED가 아니라 WAITING_FINAL_REVIEW로
    # 멈춘다. 그래도 결과 파일은 이미 저장돼 있으므로 상태와 무관하게 Artifact를 남긴다
    # (storage_key 중복 검사로 멱등).
    artifact_payload = (event.result or {}).get("artifact")
    if artifact_payload:
        existing = await db.scalar(
            select(Artifact).where(Artifact.storage_key == artifact_payload["storage_key"])
        )
        if existing is None:
            db.add(
                Artifact(
                    pipeline_run_id=run.id,
                    stage_run_id=stage.id if stage else None,
                    artifact_type=ArtifactType.FINAL.value,
                    storage_key=artifact_payload["storage_key"],
                    mime_type=artifact_payload.get("mime_type", "text/csv"),
                    size_bytes=artifact_payload.get("size_bytes"),
                    checksum=artifact_payload.get("checksum"),
                    pii_scan_status=PiiScanStatus.PASSED.value,
                    created_at=now,
                )
            )
    await db.commit()
    return True


async def record_status(
    db: AsyncSession,
    *,
    run_id: int,
    celery_task_id: str,
    run_status: PipelineRunStatus,
    progress_percent: int,
    message: str,
    current_stage: str | None = None,
    stage_status: StageRunStatus | None = None,
    result: dict | None = None,
    error_message: str | None = None,
    validation_result: dict | None = None,
    rollback_to_stage: str | None = None,
) -> PipelineStatusEvent | None:
    """상태를 DB에 쓰고, 성공하면 화면 갱신용으로 발행한다.

    run을 못 찾거나 celery_task_id가 안 맞으면 아무것도 발행하지 않고 None을 돌려준다.
    """
    event = PipelineStatusEvent(
        run_id=run_id,
        celery_task_id=celery_task_id,
        run_status=run_status,
        current_stage=current_stage,
        stage_status=stage_status,
        progress_percent=progress_percent,
        message=message,
        result=result,
        error_message=error_message,
        validation_result=validation_result,
        rollback_to_stage=rollback_to_stage,
        occurred_at=utcnow(),
    )
    if not await persist_status_event(db, event):
        return None
    publish_to_screen(event)
    return event
