from datetime import datetime

from pydantic import BaseModel, Field

from app.domains.pipeline.model import (
    DataRequestStatus,
    EventType,
    PipelineRunStatus,
    StageRunStatus,
)


class CreateDataRequestRequest(BaseModel):
    raw_requirement: str = Field(min_length=1, max_length=8000)
    title: str | None = Field(default=None, max_length=200)
    requester_name: str = Field(default="프론트엔드 데모 요청자", min_length=1, max_length=80)


class CreateDataRequestResponse(BaseModel):
    request_no: str
    run_id: int
    request_status: DataRequestStatus
    run_status: PipelineRunStatus
    current_stage: str
    created_at: datetime


class RunStageResponse(BaseModel):
    stage_code: str
    status: StageRunStatus
    executor: str
    created_at: datetime


class RunEventResponse(BaseModel):
    id: int
    event_type: EventType
    severity: str
    message: str
    payload: dict
    occurred_at: datetime


class PipelineRunResponse(BaseModel):
    run_id: int
    request_no: str
    request_title: str
    raw_requirement: str
    request_status: DataRequestStatus
    run_status: PipelineRunStatus
    current_stage: str | None
    progress_percent: int
    created_at: datetime
    updated_at: datetime
    stages: list[RunStageResponse]
    events: list[RunEventResponse]
