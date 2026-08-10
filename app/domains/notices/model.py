import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NoticeStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class Notice(Base):
    __tablename__ = "notices"
    __table_args__ = (
        Index("idx_notices_created_by", "created_by_employee_id"),
        Index("idx_notices_updated_by", "updated_by_employee_id"),
        Index(
            "idx_notices_status_published",
            "status",
            text("published_at DESC"),
            text("id DESC"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[NoticeStatus] = mapped_column(
        SAEnum(NoticeStatus, native_enum=False, length=20),
        nullable=False,
        server_default=text("'DRAFT'"),
    )
    created_by_employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("service.employees.id"), nullable=False
    )
    updated_by_employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("service.employees.id"), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
