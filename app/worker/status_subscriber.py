import asyncio
import logging

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.time_utils import utcnow
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.domains.pipeline.model import (
    Artifact,
    ArtifactType,
    DataRequest,
    DataRequestStatus,
    EventType,
    PipelineEvent,
    PipelineRun,
    PipelineRunStatus,
    StageRun,
    StageRunStatus,
    PiiScanStatus,
)
from app.worker.status_event import PipelineStatusEvent


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
        stage = await db.scalar(
            select(StageRun).where(
                StageRun.pipeline_run_id == run.id,
                StageRun.stage_code == event.current_stage,
            )
        )
        if stage is not None:
            stage.status = event.stage_status.value
            stage.executor_reference = event.celery_task_id
            if event.stage_status == StageRunStatus.RUNNING:
                stage.started_at = stage.started_at or now
            elif event.stage_status == StageRunStatus.COMPLETED:
                stage.output_payload = event.result or {}
                stage.completed_at = now
            elif event.stage_status == StageRunStatus.FAILED:
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
    artifact_payload = (event.result or {}).get("artifact")
    if event.run_status == PipelineRunStatus.COMPLETED and artifact_payload:
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


async def run() -> None:
    logging.basicConfig(level=logging.INFO)
    redis_client = Redis.from_url(settings.worker_status_redis_url, decode_responses=True)
    pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
    await pubsub.subscribe(settings.worker_status_channel)
    logger.info("Subscribed to Redis channel %s", settings.worker_status_channel)
    try:
        async for message in pubsub.listen():
            try:
                event = PipelineStatusEvent.model_validate_json(message["data"])
                async with AsyncSessionLocal() as db:
                    if await persist_status_event(db, event):
                        payload = event.model_dump_json()
                        latest_key = f"{settings.worker_status_key_prefix}:{event.run_id}"
                        async with redis_client.pipeline(transaction=True) as pipe:
                            pipe.set(latest_key, payload, ex=settings.worker_status_ttl_seconds)
                            pipe.publish(settings.worker_status_sse_channel, payload)
                            await pipe.execute()
            except Exception:
                logger.exception("Failed to persist pipeline status event")
    finally:
        await pubsub.unsubscribe(settings.worker_status_channel)
        await pubsub.aclose()
        await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(run())
