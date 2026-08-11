from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.pipeline.model import (
    DataRequestStatus,
    EventType,
    FailureCode,
    PipelineRunStatus,
    ReviewDecision,
    StageRunStatus,
)


class ContractCreateRequest(BaseModel):
    contract_no: str | None = Field(default=None, max_length=60)
    start_date: date | None = None
    end_date: date | None = None
    delivery_due_at: datetime | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> "ContractCreateRequest":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("계약 종료일은 시작일보다 빠를 수 없습니다.")
        if self.delivery_due_at and self.end_date and self.delivery_due_at.date() > self.end_date:
            raise ValueError("최종 납기일은 계약 종료일 이후일 수 없습니다.")
        return self


class ClientCreateRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=200)
    business_registration_number: str | None = Field(default=None, max_length=30)
    contact_name: str | None = Field(default=None, max_length=80)
    contact_email: str | None = Field(default=None, max_length=254)
    contact_phone: str | None = Field(default=None, max_length=40)


class CreateDataRequestRequest(BaseModel):
    raw_requirement: str = Field(min_length=1, max_length=8000)
    title: str | None = Field(default=None, max_length=200)
    requester_name: str = Field(default="프론트엔드 데모 요청자", min_length=1, max_length=80)
    client: ClientCreateRequest | None = None
    contract: ContractCreateRequest | None = None
    data_sensitivity: Literal["NONE", "POSSIBLE", "UNKNOWN"] = "UNKNOWN"


class CreateDataRequestResponse(BaseModel):
    request_no: str
    contract_no: str | None = None
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


class CreateEmailDeliveryRequest(BaseModel):
    recipient: str = Field(min_length=3, max_length=254)
    # DB 담당자가 CHECK를 생성할 때까지 코드 계약도 현재 확정된 값만 허용한다.
    delivery_type: Literal["SELECTION_SAMPLE", "FINAL_ARTIFACT"] = "SELECTION_SAMPLE"
    template_version: str = Field(default="v1", min_length=1, max_length=50)


class EmailDeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    delivery_id: str
    run_id: int
    stage_attempt_no: int
    delivery_type: str
    recipient: str
    status: str
    idempotency_key: str
    sample_sha256: str
    template_version: str
    provider_message_id: str | None = None
    failure_code: str | None = None
    created_at: datetime
    updated_at: datetime


class CustomerApiKeyResponse(BaseModel):
    endpoint_url: str
    api_key: str
    key_last4: str
    contract_no: str


class InternalDeliveryLookupResponse(BaseModel):
    """Spring(고객 API)이 이 정보로 직접 S3 presign한다. FastAPI는 S3를 안 건드린다."""

    storage_key: str
    mime_type: str
    artifact_filename: str


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
    # 요구사항 분석 단계 승인 시에만 사용. AI가 판단한 전달 설정을 실무자가 덮어쓴다.
    delivery_channel: Literal["email", "api"] | None = None
    output_formats: list[Literal["csv", "visualization", "report"]] | None = Field(
        default=None, min_length=1
    )


class StageReviewResponse(BaseModel):
    run_id: int
    reviewed_stage: str
    decision: ReviewDecision
    run_status: PipelineRunStatus
    # 승인 후 이어서 진행할 단계. 최종 승인이면 None.
    next_stage: str | None
    rollback_to_stage: str | None
    celery_task_id: str | None
