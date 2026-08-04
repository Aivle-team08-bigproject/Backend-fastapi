from datetime import datetime

from pydantic import BaseModel, Field

from app.domains.pipeline.model import (
    DataRequestStatus,
    EventType,
    FailureCode,
    PipelineRunStatus,
    ReviewDecision,
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
    celery_task_id: str
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
    celery_task_id: str | None
    created_at: datetime
    updated_at: datetime
    stages: list[RunStageResponse]
    events: list[RunEventResponse]


class SamplePreviewColumn(BaseModel):
    name: str
    data_type: str
    is_derived: bool
    source_columns: list[str]
    description: str


class SamplePreviewMetadata(BaseModel):
    is_synthetic: bool
    sample_count: int
    notice: str | None = None


class SamplePreviewReviewSummary(BaseModel):
    requires_confirmation: bool
    confirmation_terms: list[str]
    has_catalog_issues: bool
    catalog_issue_count: int


class SamplePreviewResponse(BaseModel):
    run_id: int
    stage: str
    attempt_no: int
    columns: list[SamplePreviewColumn]
    rows: list[dict]
    metadata: SamplePreviewMetadata
    selected_tables: list[dict]
    source_columns: list[dict]
    derived_columns: list[dict]
    selection_query: dict
    interpretations: list[dict]
    catalog_issues: list[dict]
    catalog_matches: list[dict]
    review_summary: SamplePreviewReviewSummary


class StageReviewRequest(BaseModel):
    """단계 산출물에 대한 사람 검토 결과(HITL)."""

    approved: bool
    feedback: str | None = Field(default=None, max_length=4000)
    # 선택값이 있으면 실패 정책표로 롤백 단계를 정한다. 없으면 현재 HITL 게이트 기준으로
    # 요구사항→요구사항 분석, 샘플→선별, 최종 산출물→가공 단계부터 다시 실행한다.
    failure_code: FailureCode | None = None


class StageReviewResponse(BaseModel):
    run_id: int
    reviewed_stage: str
    decision: ReviewDecision
    run_status: PipelineRunStatus
    # 승인 후 이어서 진행할 단계. 최종 승인이면 None.
    next_stage: str | None
    rollback_to_stage: str | None
    celery_task_id: str | None
