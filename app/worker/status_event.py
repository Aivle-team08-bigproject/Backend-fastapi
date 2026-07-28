from datetime import datetime

from pydantic import BaseModel, Field

from app.domains.pipeline.model import PipelineRunStatus, StageRunStatus


class PipelineStatusEvent(BaseModel):
    run_id: int
    celery_task_id: str = Field(min_length=1, max_length=255)
    run_status: PipelineRunStatus
    current_stage: str | None = None
    stage_status: StageRunStatus | None = None
    progress_percent: int = Field(ge=0, le=100)
    message: str
    result: dict | None = None
    error_message: str | None = None
    occurred_at: datetime
