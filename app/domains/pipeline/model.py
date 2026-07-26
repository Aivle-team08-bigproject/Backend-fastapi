"""데모 ERD를 현재 FastAPI 구조에 맞춘 서비스 운영 모델.

기존 `app/models/requirements.py`는 별도 동기 legacy Base를 사용한다. 이 모델은
비동기 서비스 DB Base에서 독립적인 `data_requests`를 사용해 cross-metadata FK를 피한다.
"""

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Date, DateTime, Enum as SAEnum, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.time_utils import utcnow
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


class EventType(str, enum.Enum):
    PROGRESS = "progress"
    AGENT_LOG = "agent_log"
    ARTIFACT_READY = "artifact_ready"
    FAILED = "failed"


class ReviewDecision(str, enum.Enum):
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"


class DeliveryChannel(str, enum.Enum):
    FILE_DOWNLOAD = "FILE_DOWNLOAD"
    API = "API"
    EMAIL = "EMAIL"


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)
    business_registration_number: Mapped[str | None] = mapped_column(String(30), unique=True)
    contact_name: Mapped[str | None] = mapped_column(String(80))
    contact_email: Mapped[str | None] = mapped_column(String(254))
    contact_phone: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[ClientStatus] = mapped_column(
        SAEnum(ClientStatus, native_enum=False, length=20), nullable=False, default=ClientStatus.ACTIVE
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utcnow, onupdate=utcnow)

    requests: Mapped[list["DataRequest"]] = relationship(back_populates="client")


class SourceDataset(Base):
    """데모 원천 CSV의 적재 이력과 재현에 필요한 메타데이터."""

    __tablename__ = "source_datasets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_code: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_file: Mapped[str] = mapped_column(String(500), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    loaded_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utcnow)


class SourceCustomer(Base):
    __tablename__ = "source_customers"

    customer_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("source_datasets.id"), nullable=False, index=True)
    gender: Mapped[str | None] = mapped_column(String(20))
    age_band: Mapped[str | None] = mapped_column(String(30))
    resident_region: Mapped[str | None] = mapped_column(String(120), index=True)
    postal_code: Mapped[str | None] = mapped_column(String(20))
    occupation: Mapped[str | None] = mapped_column(String(80))
    annual_income_band: Mapped[str | None] = mapped_column(String(80))
    marital_status: Mapped[str | None] = mapped_column(String(30))


class SourceCard(Base):
    __tablename__ = "source_cards"

    card_number_masked: Mapped[str] = mapped_column(String(40), primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("source_datasets.id"), nullable=False, index=True)
    # 원본 파일은 부분 추출본일 수 있으므로 고객 마스터 FK를 강제하지 않는다.
    customer_id: Mapped[str | None] = mapped_column(String(40), index=True)
    card_product_code: Mapped[str | None] = mapped_column(String(40))
    card_issue_month: Mapped[str | None] = mapped_column(String(7))
    credit_limit_band: Mapped[str | None] = mapped_column(String(40))
    card_status: Mapped[str | None] = mapped_column(String(30))
    card_status_changed_month: Mapped[str | None] = mapped_column(String(7))
    signup_channel: Mapped[str | None] = mapped_column(String(40))


class SourceMerchant(Base):
    __tablename__ = "source_merchants"

    merchant_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("source_datasets.id"), nullable=False, index=True)
    merchant_name: Mapped[str | None] = mapped_column(String(200))
    business_registration_number: Mapped[str | None] = mapped_column(String(40))
    mcc_code: Mapped[str | None] = mapped_column(String(10), index=True)
    franchise_hq_code: Mapped[str | None] = mapped_column(String(40))
    merchant_region: Mapped[str | None] = mapped_column(String(120), index=True)
    merchant_open_month: Mapped[str | None] = mapped_column(String(7))
    fee_tier_code: Mapped[str | None] = mapped_column(String(40))
    merchant_status: Mapped[str | None] = mapped_column(String(30))
    merchant_status_changed_month: Mapped[str | None] = mapped_column(String(7))


class SourceMccCode(Base):
    __tablename__ = "source_mcc_codes"

    mcc_code: Mapped[str] = mapped_column(String(10), primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("source_datasets.id"), nullable=False, index=True)
    mcc_name: Mapped[str] = mapped_column(String(120), nullable=False)


class SourceTransaction(Base):
    __tablename__ = "source_transactions"

    transaction_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("source_datasets.id"), nullable=False, index=True)
    # 거래 파일은 마스터 파일보다 넓은 기간/범위를 포함할 수 있다. 원본 보존을 위해
    # 참조 무결성은 정제 단계에서 검증하고 이 적재 단계에서는 FK를 강제하지 않는다.
    card_number_masked: Mapped[str | None] = mapped_column(String(40), index=True)
    merchant_id: Mapped[str | None] = mapped_column(String(40), index=True)
    mcc_code: Mapped[str | None] = mapped_column(String(10), index=True)
    transaction_datetime: Mapped[datetime] = mapped_column(DateTime(), nullable=False, index=True)
    approval_status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    decline_reason_code: Mapped[str | None] = mapped_column(String(50))
    transaction_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    krw_converted_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    applied_exchange_rate: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    merchant_country_code: Mapped[str | None] = mapped_column(String(3))
    installment_months: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approval_channel: Mapped[str | None] = mapped_column(String(40))
    pos_entry_mode: Mapped[str | None] = mapped_column(String(40))
    auth_method: Mapped[str | None] = mapped_column(String(40))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    device_id: Mapped[str | None] = mapped_column(String(40))
    terminal_id: Mapped[str | None] = mapped_column(String(40))


class DataRequest(Base):
    __tablename__ = "data_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_no: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), index=True)
    requester_name: Mapped[str | None] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    business_purpose: Mapped[str | None] = mapped_column(Text)
    raw_requirement: Mapped[str] = mapped_column(Text, nullable=False)
    output_formats: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    delivery_channels: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    usage_period: Mapped[str | None] = mapped_column(String(200))
    analysis_condition: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    sample_email: Mapped[str | None] = mapped_column(String(254))
    status: Mapped[DataRequestStatus] = mapped_column(
        SAEnum(DataRequestStatus, native_enum=False, length=30), nullable=False, default=DataRequestStatus.DRAFT, index=True
    )
    current_stage: Mapped[str | None] = mapped_column(String(80), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utcnow, onupdate=utcnow)

    client: Mapped[Client] = relationship(back_populates="requests")
    runs: Mapped[list["PipelineRun"]] = relationship(back_populates="data_request", cascade="all, delete-orphan")
    contracts: Mapped[list["Contract"]] = relationship(back_populates="data_request", cascade="all, delete-orphan")


class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_request_id: Mapped[int] = mapped_column(ForeignKey("data_requests.id"), nullable=False, index=True)
    contract_no: Mapped[str] = mapped_column(String(60), nullable=False, unique=True, index=True)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    billing_type: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[ContractStatus] = mapped_column(
        SAEnum(ContractStatus, native_enum=False, length=20), nullable=False, default=ContractStatus.DRAFT, index=True
    )
    signed_at: Mapped[datetime | None] = mapped_column(DateTime())
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utcnow, onupdate=utcnow)

    data_request: Mapped[DataRequest] = relationship(back_populates="contracts")
    api_keys: Mapped[list["ContractApiKey"]] = relationship(back_populates="contract", cascade="all, delete-orphan")


class ContractApiKey(Base):
    __tablename__ = "contract_api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("contracts.id"), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    key_last4: Mapped[str] = mapped_column(String(4), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime())
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utcnow)

    contract: Mapped[Contract] = relationship(back_populates="api_keys")
    usage_logs: Mapped[list["ApiUsageLog"]] = relationship(back_populates="api_key", cascade="all, delete-orphan")


class ApiUsageLog(Base):
    __tablename__ = "api_usage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    api_key_id: Mapped[int] = mapped_column(ForeignKey("contract_api_keys.id"), nullable=False, index=True)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_time_ms: Mapped[int | None] = mapped_column(Integer)
    requested_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utcnow, index=True)

    api_key: Mapped[ContractApiKey] = relationship(back_populates="usage_logs")


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = (UniqueConstraint("data_request_id", "attempt_no", name="uq_pipeline_run_attempt"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_request_id: Mapped[int] = mapped_column(ForeignKey("data_requests.id"), nullable=False, index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[PipelineRunStatus] = mapped_column(
        SAEnum(PipelineRunStatus, native_enum=False, length=40), nullable=False, default=PipelineRunStatus.QUEUED, index=True
    )
    current_stage: Mapped[str | None] = mapped_column(String(80), index=True)
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime())
    review_comment: Mapped[str | None] = mapped_column(Text)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime())
    started_at: Mapped[datetime | None] = mapped_column(DateTime())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime())
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utcnow, onupdate=utcnow)

    data_request: Mapped[DataRequest] = relationship(back_populates="runs")
    stage_runs: Mapped[list["StageRun"]] = relationship(back_populates="pipeline_run", cascade="all, delete-orphan")
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="pipeline_run", cascade="all, delete-orphan")


class StageRun(Base):
    __tablename__ = "stage_runs"
    __table_args__ = (UniqueConstraint("pipeline_run_id", "stage_code", "attempt_no", name="uq_stage_run_attempt"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False, index=True)
    retry_of_id: Mapped[int | None] = mapped_column(ForeignKey("stage_runs.id"), index=True)
    stage_code: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[StageRunStatus] = mapped_column(
        SAEnum(StageRunStatus, native_enum=False, length=20), nullable=False, default=StageRunStatus.PENDING, index=True
    )
    model_name: Mapped[str | None] = mapped_column(String(120))
    input_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    output_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    validation_result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime())
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utcnow)

    pipeline_run: Mapped[PipelineRun] = relationship(back_populates="stage_runs")
    events: Mapped[list["PipelineEvent"]] = relationship(back_populates="stage_run", cascade="all, delete-orphan")
    metrics: Mapped[list["AgentMetric"]] = relationship(back_populates="stage_run", cascade="all, delete-orphan")


class PipelineEvent(Base):
    __tablename__ = "pipeline_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False, index=True)
    stage_run_id: Mapped[int | None] = mapped_column(ForeignKey("stage_runs.id"), index=True)
    event_type: Mapped[EventType] = mapped_column(
        SAEnum(EventType, native_enum=False, length=30), nullable=False, index=True
    )
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="INFO")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utcnow, index=True)

    stage_run: Mapped[StageRun | None] = relationship(back_populates="events")


class AgentMetric(Base):
    __tablename__ = "agent_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stage_run_id: Mapped[int] = mapped_column(ForeignKey("stage_runs.id"), nullable=False, index=True)
    agent_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    model_name: Mapped[str | None] = mapped_column(String(120))
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False, default="SUCCEEDED")
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utcnow, index=True)

    stage_run: Mapped[StageRun] = relationship(back_populates="metrics")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False, index=True)
    stage_run_id: Mapped[int | None] = mapped_column(ForeignKey("stage_runs.id"), index=True)
    artifact_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    checksum: Mapped[str | None] = mapped_column(String(128))
    pii_scan_status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utcnow)

    pipeline_run: Mapped[PipelineRun] = relationship(back_populates="artifacts")
    reviews: Mapped[list["Review"]] = relationship(back_populates="artifact", cascade="all, delete-orphan")
    deliveries: Mapped[list["Delivery"]] = relationship(back_populates="artifact", cascade="all, delete-orphan")


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_request_id: Mapped[int] = mapped_column(ForeignKey("data_requests.id"), nullable=False, index=True)
    stage_run_id: Mapped[int | None] = mapped_column(ForeignKey("stage_runs.id"), index=True)
    artifact_id: Mapped[int | None] = mapped_column(ForeignKey("artifacts.id"), index=True)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), nullable=False, index=True)
    review_type: Mapped[str] = mapped_column(String(40), nullable=False)
    decision: Mapped[ReviewDecision] = mapped_column(
        SAEnum(ReviewDecision, native_enum=False, length=30), nullable=False
    )
    feedback: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utcnow)

    artifact: Mapped[Artifact | None] = relationship(back_populates="reviews")


class Delivery(Base):
    __tablename__ = "deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_request_id: Mapped[int] = mapped_column(ForeignKey("data_requests.id"), nullable=False, index=True)
    artifact_id: Mapped[int] = mapped_column(ForeignKey("artifacts.id"), nullable=False, index=True)
    channel: Mapped[DeliveryChannel] = mapped_column(
        SAEnum(DeliveryChannel, native_enum=False, length=30), nullable=False
    )
    recipient: Mapped[str | None] = mapped_column(String(254))
    secret_reference: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING", index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime())
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utcnow)

    artifact: Mapped[Artifact] = relationship(back_populates="deliveries")
