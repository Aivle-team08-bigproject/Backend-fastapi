import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum as SAEnum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NoticeStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class Notice(Base):
    __tablename__ = "notices"
    __table_args__ = (
        Index("ix_notices_status_published_at_id", "status", "published_at", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[NoticeStatus] = mapped_column(
        SAEnum(NoticeStatus, native_enum=False, length=20), nullable=False, default=NoticeStatus.DRAFT
    )
    created_by_employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("service.employees.id"), nullable=False, index=True
    )
    updated_by_employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("service.employees.id"), nullable=False, index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
