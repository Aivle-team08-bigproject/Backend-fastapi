from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.domains.pipeline.model import (
    PipelineRunStatus,
    ProcessingStepCode,
    ProcessingStepStatus,
    SelectionStepCode,
    SelectionStepStatus,
    StageRunStatus,
)


class PipelineStatusEvent(BaseModel):
    event_id: int | None = Field(default=None, ge=1)
    run_id: int
    celery_task_id: str = Field(min_length=1, max_length=255)
    run_status: PipelineRunStatus
    current_stage: str | None = None
    stage_status: StageRunStatus | None = None
    selection_step: SelectionStepCode | None = None
    selection_step_status: SelectionStepStatus | None = None
    processing_step: ProcessingStepCode | None = None
    processing_step_status: ProcessingStepStatus | None = None
    attempt_no: int | None = Field(default=None, ge=1)
    stage_run_id: int | None = Field(default=None, ge=1)
    step_metadata: dict | None = None
    progress_percent: int = Field(ge=0, le=100)
    message: str
    result: dict | None = None
    error_message: str | None = None
    # Supervisor의 산출물 검증 결과. stage_runs.validation_result에 그대로 저장된다.
    validation_result: dict | None = None
    # 검증 실패 시 되돌아갈 단계(FailureCode -> StageName 판정 결과)
    rollback_to_stage: str | None = None
    occurred_at: datetime

    @model_validator(mode="after")
    def validate_selection_step_fields(self) -> "PipelineStatusEvent":
        if (self.selection_step is None) != (self.selection_step_status is None):
            raise ValueError(
                "selection_step and selection_step_status must be set together"
            )
        if self.selection_step is not None and self.attempt_no is None:
            raise ValueError("selection step event requires attempt_no")
        if (self.processing_step is None) != (self.processing_step_status is None):
            raise ValueError(
                "processing_step and processing_step_status must be set together"
            )
        if self.processing_step is not None and self.attempt_no is None:
            raise ValueError("processing step event requires attempt_no")
        return self
