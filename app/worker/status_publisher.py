from redis import Redis

from app.common.time_utils import utcnow
from app.core.config import settings
from app.domains.pipeline.model import PipelineRunStatus, StageRunStatus
from app.worker.status_event import PipelineStatusEvent


_redis_client = Redis.from_url(settings.worker_status_redis_url, decode_responses=True)


def publish_status(
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
) -> PipelineStatusEvent:
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
        occurred_at=utcnow(),
    )
    payload = event.model_dump_json()
    latest_key = f"{settings.worker_status_key_prefix}:{run_id}"
    with _redis_client.pipeline() as pipe:
        pipe.set(latest_key, payload, ex=settings.worker_status_ttl_seconds)
        pipe.publish(settings.worker_status_channel, payload)
        pipe.execute()
    return event
