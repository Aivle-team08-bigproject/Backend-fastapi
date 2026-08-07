"""service 스키마 — 비즈니스+실행+산출 테이블 (V10~V12 DDL과 1:1 대응)."""

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Numeric,
    SmallInteger, String, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ClientStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class DataRequestStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_REVIEW = "WAITING_REVIEW"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class ContractStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    TERMINATED = "TERMINATED"


class ApiKeyStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class PipelineRunStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_REQUIREMENT_REVIEW = "WAITING_REQUIREMENT_REVIEW"
    WAITING_SAMPLE_REVIEW = "WAITING_SAMPLE_REVIEW"
    WAITING_FINAL_REVIEW = "WAITING_FINAL_REVIEW"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class StageRunStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    ROLLED_BACK = "ROLLED_BACK"


class SelectionStepCode(str, enum.Enum):
    """DATA_SELECTION 내부에서 실제로 순차 실행되는 의미적 단계."""

    SOURCE_COLUMN_SELECTION = "SOURCE_COLUMN_SELECTION"
    DERIVED_COLUMN_DESIGN = "DERIVED_COLUMN_DESIGN"
    SYNTHETIC_SAMPLE_GENERATION = "SYNTHETIC_SAMPLE_GENERATION"


class SelectionStepStatus(str, enum.Enum):
    """선별 서브스텝 상태. 상위 StageRun과 별도 계약으로 관리한다."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class ProcessingStepCode(str, enum.Enum):
    """DATA_PROCESSING 내부의 LLM 계획 수립 단계."""

    DEDUPLICATION_PLAN = "DEDUPLICATION_PLAN"
    MISSING_VALUE_PLAN = "MISSING_VALUE_PLAN"
    DERIVED_COLUMN_ORDER = "DERIVED_COLUMN_ORDER"
    FINAL_COLUMN_VALIDATION = "FINAL_COLUMN_VALIDATION"


class ProcessingStepStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class AnalysisStepCode(str, enum.Enum):
    """REQUIREMENT_ANALYSIS 내부에서 실제로 순차 실행되는 의미적 단계."""

    REQUEST_ANALYSIS = "REQUEST_ANALYSIS"
    REQUEST_STRUCTURING = "REQUEST_STRUCTURING"
    DATA_CATEGORIZATION = "DATA_CATEGORIZATION"


class AnalysisStepStatus(str, enum.Enum):
    """요구사항 분석 서브스텝 상태. 상위 StageRun과 별도 계약으로 관리한다."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class StageName(str, enum.Enum):
    """Supervisor가 순서대로 진행시키는 실행 단계.

    stage_runs.stage_code에 저장되는 값과 1:1로 대응한다. 데이터 조회는 별도 단계가
    아니라 DATA_PROCESSING 안에서 query 레이어(agent_runtime/query)가 담당한다.
    """

    REQUIREMENT_ANALYSIS = "REQUIREMENT_ANALYSIS"
    DATA_SELECTION = "DATA_SELECTION"
    DATA_PROCESSING = "DATA_PROCESSING"
    HITL_REVIEW = "HITL_REVIEW"


class FailureCode(str, enum.Enum):
    """단계 산출물 검증 실패 사유. 반려 시 어느 단계로 되돌릴지 판단하는 근거가 된다."""

    SCHEMA_INVALID = "SCHEMA_INVALID"
    REQUIRED_KEY_MISSING = "REQUIRED_KEY_MISSING"
    FORMAT_INVALID = "FORMAT_INVALID"
    LOGICAL_CONTRADICTION = "LOGICAL_CONTRADICTION"
    MISINTERPRETED_REQUIREMENT = "MISINTERPRETED_REQUIREMENT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    LOW_SIMILARITY_MATCH = "LOW_SIMILARITY_MATCH"
    DUPLICATED_DATA = "DUPLICATED_DATA"
    OUTLIER_DETECTED = "OUTLIER_DETECTED"
    SELECTION_RULE_INVALID = "SELECTION_RULE_INVALID"
    PROCESSING_RULE_INVALID = "PROCESSING_RULE_INVALID"
    PRIVACY_THRESHOLD_NOT_MET = "PRIVACY_THRESHOLD_NOT_MET"
    HUMAN_REJECTED = "HUMAN_REJECTED"


class EventType(str, enum.Enum):
    PROGRESS = "progress"
    AGENT_LOG = "agent_log"
    ARTIFACT_READY = "artifact_ready"
    FAILED = "failed"


class ArtifactType(str, enum.Enum):
    SAMPLE = "SAMPLE"
    FINAL = "FINAL"


class PiiScanStatus(str, enum.Enum):
    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"


class ReviewDecision(str, enum.Enum):
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"


class DeliveryChannel(str, enum.Enum):
    FILE_DOWNLOAD = "FILE_DOWNLOAD"
    API = "API"
    EMAIL = "EMAIL"


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    business_registration_number: Mapped[str | None] = mapped_column(String(30), unique=True)
    contact_name: Mapped[str | None] = mapped_column(String(80))
    contact_email: Mapped[str | None] = mapped_column(String(254))
    contact_phone: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ClientStatus.ACTIVE.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    requests: Mapped[list["DataRequest"]] = relationship(back_populates="client")


class DataRequest(Base):
    __tablename__ = "data_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    request_no: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    client_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("service.clients.id"), nullable=False, index=True)
    owner_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("service.employees.id"), index=True)
    requester_name: Mapped[str | None] = mapped_column(String(100))
    owner_name: Mapped[str | None] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    business_purpose: Mapped[str | None] = mapped_column(Text)
    raw_requirement: Mapped[str] = mapped_column(Text, nullable=False)
    output_formats: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    delivery_channels: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    usage_period: Mapped[str | None] = mapped_column(String(200))
    structured_requirement: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    data_sensitivity: Mapped[str] = mapped_column(String(20), nullable=False, default="UNKNOWN")
    analysis_condition: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    sample_email: Mapped[str | None] = mapped_column(String(254))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default=DataRequestStatus.DRAFT.value, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    client: Mapped[Client] = relationship(back_populates="requests")
    runs: Mapped[list["PipelineRun"]] = relationship(back_populates="data_request")
    contracts: Mapped[list["Contract"]] = relationship(back_populates="data_request")


class Contract(Base):
    __tablename__ = "contracts"
    __table_args__ = (
        Index(
            "uq_contracts_one_active",
            "data_request_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    data_request_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("service.data_requests.id"), nullable=False, index=True)
    contract_no: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    delivery_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    billing_type: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ContractStatus.DRAFT.value)
    renewed_from_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("service.contracts.id"))
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    data_request: Mapped[DataRequest] = relationship(back_populates="contracts")
    api_keys: Mapped[list["ContractApiKey"]] = relationship(back_populates="contract")


class ContractApiKey(Base):
    __tablename__ = "contract_api_keys"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("service.contracts.id"), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    key_last4: Mapped[str] = mapped_column(String(4), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ApiKeyStatus.ACTIVE.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    contract: Mapped[Contract] = relationship(back_populates="api_keys")
    usage_logs: Mapped[list["ApiUsageLog"]] = relationship(back_populates="api_key")


class ApiUsageLog(Base):
    __tablename__ = "api_usage_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    api_key_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("service.contract_api_keys.id"), nullable=False, index=True)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    response_status: Mapped[int | None] = mapped_column()
    response_time_ms: Mapped[int | None] = mapped_column()
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    api_key: Mapped[ContractApiKey] = relationship(back_populates="usage_logs")


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = (UniqueConstraint("data_request_id", "attempt_no", name="uq_pipeline_run_attempt"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    data_request_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("service.data_requests.id"), nullable=False, index=True)
    attempt_no: Mapped[int] = mapped_column(nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default=PipelineRunStatus.QUEUED.value, index=True)
    current_stage: Mapped[str | None] = mapped_column(String(80), index=True)
    progress_percent: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    # 검증 실패·반려 사유와, 반려 시 되돌아갈 단계(Supervisor가 다음 dispatch에서 읽는다)
    error_message: Mapped[str | None] = mapped_column(Text)
    rollback_to_stage: Mapped[str | None] = mapped_column(String(80))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # reviewed_by_id/reviewed_at/review_comment 없음(의도) — reviews 테이블이 유일 소스

    data_request: Mapped[DataRequest] = relationship(back_populates="runs")
    stage_runs: Mapped[list["StageRun"]] = relationship(back_populates="pipeline_run")
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="pipeline_run")


class StageRun(Base):
    __tablename__ = "stage_runs"
    __table_args__ = (UniqueConstraint("pipeline_run_id", "stage_code", "attempt_no", name="uq_stage_run_attempt"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pipeline_run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("service.pipeline_runs.id"), nullable=False, index=True)
    retry_of_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("service.stage_runs.id"), index=True)
    stage_code: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    attempt_no: Mapped[int] = mapped_column(nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=StageRunStatus.PENDING.value, index=True)
    executor: Mapped[str] = mapped_column(String(30), nullable=False, default="CELERY")
    executor_reference: Mapped[str | None] = mapped_column(String(255), index=True)
    model_name: Mapped[str | None] = mapped_column(String(120))
    input_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    output_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    validation_result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    pipeline_run: Mapped[PipelineRun] = relationship(back_populates="stage_runs")
    events: Mapped[list["PipelineEvent"]] = relationship(back_populates="stage_run")
    metrics: Mapped[list["AgentMetric"]] = relationship(back_populates="stage_run")


class PipelineEvent(Base):
    __tablename__ = "pipeline_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pipeline_run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("service.pipeline_runs.id"), nullable=False, index=True)
    stage_run_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("service.stage_runs.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="INFO")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    stage_run: Mapped[StageRun | None] = relationship(back_populates="events")


class AgentMetric(Base):
    __tablename__ = "agent_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stage_run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("service.stage_runs.id"), nullable=False, index=True)
    agent_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    model_name: Mapped[str | None] = mapped_column(String(120))
    input_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    latency_ms: Mapped[int | None] = mapped_column()
    outcome: Mapped[str] = mapped_column(String(30), nullable=False, default="SUCCEEDED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    stage_run: Mapped[StageRun] = relationship(back_populates="metrics")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pipeline_run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("service.pipeline_runs.id"), nullable=False, index=True)
    stage_run_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("service.stage_runs.id"))
    artifact_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    checksum: Mapped[str | None] = mapped_column(String(128))
    pii_scan_status: Mapped[str] = mapped_column(String(30), nullable=False, default=PiiScanStatus.PENDING.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    pipeline_run: Mapped[PipelineRun] = relationship(back_populates="artifacts")
    reviews: Mapped[list["Review"]] = relationship(back_populates="artifact")
    deliveries: Mapped[list["Delivery"]] = relationship(back_populates="artifact")


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    data_request_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("service.data_requests.id"), nullable=False, index=True)
    stage_run_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("service.stage_runs.id"), index=True)
    artifact_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("service.artifacts.id"), index=True)
    reviewer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("service.employees.id"), nullable=False, index=True)
    reviewer_name: Mapped[str | None] = mapped_column(String(100))
    review_type: Mapped[str] = mapped_column(String(40), nullable=False)
    decision: Mapped[str] = mapped_column(String(30), nullable=False)
    feedback: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    artifact: Mapped[Artifact | None] = relationship(back_populates="reviews")


class Delivery(Base):
    __tablename__ = "deliveries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    data_request_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("service.data_requests.id"), nullable=False, index=True)
    artifact_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("service.artifacts.id"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    recipient: Mapped[str | None] = mapped_column(String(254))
    secret_reference: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING", index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    artifact: Mapped[Artifact] = relationship(back_populates="deliveries")
