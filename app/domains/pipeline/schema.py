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
    execution_id: str
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
    # 산출물 메일 수신자 기본값. 고객사 담당자 연락처가 등록돼 있을 때만 채운다.
    # 값이 없으면 화면은 빈 칸으로 두고 사용자가 직접 입력해야 한다 — 로그인한
    # 실무자 이메일로 대신 채우면 고객 대신 자기 자신에게 보내는 사고가 난다.
    client_contact_email: str | None = None
    raw_requirement: str
    request_status: DataRequestStatus
    run_status: PipelineRunStatus
    current_stage: str | None
    progress_percent: int
    execution_id: str | None
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
    delivery_type: Literal["SELECTION_SAMPLE", "FINAL_ARTIFACT"] = "SELECTION_SAMPLE"
    template_version: str = Field(default="v1", min_length=1, max_length=50)
    # API 키는 발급 응답에서만 평문으로 노출되므로, 사용자가 같은 화면에서
    # 메일을 보낼 때만 요청 본문에 실어 Spring까지 전달한다. DB에는 저장하지 않는다.
    api_endpoint_url: str | None = Field(default=None, max_length=2048)
    api_key: str | None = Field(default=None, min_length=8, max_length=255)

    @model_validator(mode="after")
    def validate_api_credentials(self) -> "CreateEmailDeliveryRequest":
        if bool(self.api_endpoint_url) != bool(self.api_key):
            raise ValueError("api_endpoint_url과 api_key는 함께 입력해야 합니다.")
        if self.delivery_type == "FINAL_ARTIFACT" and not self.api_endpoint_url:
            raise ValueError("최종 산출물 메일에는 API URL과 API Key가 필요합니다.")
        if self.delivery_type != "FINAL_ARTIFACT" and (self.api_endpoint_url or self.api_key):
            raise ValueError("API 인증정보는 최종 산출물 메일에서만 사용할 수 있습니다.")
        return self


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
    output_formats: list[Literal["csv"]] | None = Field(
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
    execution_id: str | None


class AdminPipelineRecoveryRequest(BaseModel):
    mode: Literal["RESTART", "REQUIREMENT_ANALYSIS"]


class AdminPipelineRecoveryResponse(BaseModel):
    run_id: int
    mode: Literal["RESTART", "REQUIREMENT_ANALYSIS"]
    run_status: PipelineRunStatus
    next_stage: str
    execution_id: str | None
