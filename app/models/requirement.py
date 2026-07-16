from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.enums import Priority, RequirementStatus


def utcnow() -> datetime:
    return datetime.utcnow()


class Requirement(Base):
    __tablename__ = "requirements"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    business_purpose: Mapped[str | None] = mapped_column(Text)
    acceptance_criteria: Mapped[str | None] = mapped_column(Text)
    requester: Mapped[str | None] = mapped_column(String(100), index=True)
    owner: Mapped[str | None] = mapped_column(String(100), index=True)
    priority: Mapped[Priority] = mapped_column(Enum(Priority), default=Priority.MEDIUM, index=True)
    status: Mapped[RequirementStatus] = mapped_column(
        Enum(RequirementStatus), default=RequirementStatus.DRAFT, index=True
    )
    due_date: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[str] = mapped_column(String(100), default="system")
    updated_by: Mapped[str] = mapped_column(String(100), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    tasks = relationship("Task", back_populates="requirement", cascade="all, delete-orphan")
    status_history = relationship(
        "RequirementStatusHistory",
        back_populates="requirement",
        cascade="all, delete-orphan",
        order_by="RequirementStatusHistory.changed_at",
    )


class RequirementStatusHistory(Base):
    __tablename__ = "requirement_status_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_status: Mapped[RequirementStatus | None] = mapped_column(Enum(RequirementStatus))
    to_status: Mapped[RequirementStatus] = mapped_column(Enum(RequirementStatus), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    changed_by: Mapped[str] = mapped_column(String(100), default="system")
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    requirement = relationship("Requirement", back_populates="status_history")
