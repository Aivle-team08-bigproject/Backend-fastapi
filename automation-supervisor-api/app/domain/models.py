from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.enums import JobStatus, StageStatus


def utcnow() -> datetime:
    # datetime.utcnow()는 시간대 정보가 없는 naive 값이라 TIMESTAMPTZ 컬럼과
    # 어긋난다. mart/anon/service 전 계층이 UTC 기준 timestamptz다.
    return datetime.now(timezone.utc)


class AutomationJob(Base):
    __tablename__ = "automation_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    requirement_id: Mapped[int | None] = mapped_column(Integer, index=True)
    raw_requirement: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default=JobStatus.QUEUED.value, index=True)
    current_stage: Mapped[str | None] = mapped_column(String(80), index=True)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    qa_iteration: Mapped[int] = mapped_column(Integer, default=0)
    max_qa_iterations: Mapped[int] = mapped_column(Integer, default=3)
    rollback_to_stage: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    final_result: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    stages = relationship("AutomationStageRun", back_populates="job", cascade="all, delete-orphan")


class AutomationStageRun(Base):
    __tablename__ = "automation_stage_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("automation_jobs.id", ondelete="CASCADE"), index=True)
    stage_name: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(30), default=StageStatus.PENDING.value, index=True)
    model_name: Mapped[str | None] = mapped_column(String(120))
    input_payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    output_payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    validation_result: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)
    run_order: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job = relationship("AutomationJob", back_populates="stages")
    caches = relationship("StageArtifactCache", back_populates="stage", cascade="all, delete-orphan")


class StageArtifactCache(Base):
    __tablename__ = "stage_artifact_caches"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    stage_id: Mapped[int] = mapped_column(ForeignKey("automation_stage_runs.id", ondelete="CASCADE"), index=True)
    cache_key: Mapped[str] = mapped_column(String(128), index=True)
    artifact: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    stage = relationship("AutomationStageRun", back_populates="caches")
