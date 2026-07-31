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
    # Supervisor의 산출물 검증 결과. stage_runs.validation_result에 그대로 저장된다.
    validation_result: dict | None = None
    # 검증 실패 시 되돌아갈 단계(FailureCode -> StageName 판정 결과)
    rollback_to_stage: str | None = None
    occurred_at: datetime
