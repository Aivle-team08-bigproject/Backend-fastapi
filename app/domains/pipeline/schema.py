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


class CreateDataRequestContract(BaseModel):
    """캘린더 마감일 큐가 읽는 값. 계약 본 레코드(Contract 테이블)와는 별개 — analysis_condition에만 적재된다.

    datetime으로 받아 Pydantic이 API 경계에서 형식을 검증한다 — 원래 str이었을 때는
    형식 검증이 전혀 없어서 깨진 값이 그대로 DB에 저장됐고, 대시보드가 그 값을
    SQL에서 timestamptz로 CAST할 때 그제서야 500으로 터졌다(그것도 owner 필터가
    없는 관리자 조회에서는 그 값을 만든 사람이 아닌 다른 사람의 화면이 죽었다).
    """

    delivery_due_at: datetime | None = None


class CreateDataRequestRequest(BaseModel):
    raw_requirement: str = Field(min_length=1, max_length=8000)
    title: str | None = Field(default=None, max_length=200)
    requester_name: str = Field(default="프론트엔드 데모 요청자", min_length=1, max_length=80)
    contract: CreateDataRequestContract | None = None


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
    failure: dict | None = None


class RunEventResponse(BaseModel):
    id: int
    event_type: EventType
    severity: str
    message: str
    payload: dict
    occurred_at: datetime


class RequirementAnalysisResponse(BaseModel):
    usage_purpose: str
    requested_data_sentence: str
    categories: dict
    delivery_channel: str
    output_formats: list[str]


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
    error_message: str | None = None
    failure: dict | None = None
    stages: list[RunStageResponse]
    events: list[RunEventResponse]
    requirement_analysis: RequirementAnalysisResponse | None = None


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


class ProcessingResultResponse(BaseModel):
    run_id: int
    stage: str
    attempt_no: int
    api_result: dict
    processed_columns: list[str]
    quality_report: dict
    processing_explanation: dict
    visualization: dict | None = None
    report: dict | None = None
    processing_plan: dict | None = None


class StageReviewRequest(BaseModel):
    """단계 산출물에 대한 사람 검토 결과(HITL)."""

    approved: bool
    # FAILED 실행을 rollback_to_stage부터 다시 큐에 넣는다.
    retry: bool = False
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
