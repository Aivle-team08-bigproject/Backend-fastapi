"""service 스키마 — 화면 캐시/대시보드 read model (V12 DDL과 1:1 대응)."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TaskViewSnapshot(Base):
    __tablename__ = "task_view_snapshots"
    __table_args__ = (UniqueConstraint("data_request_id", "view_code", name="uq_task_view"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    data_request_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("service.data_requests.id"), nullable=False, index=True)
    view_code: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DashboardAlert(Base):
    __tablename__ = "dashboard_alerts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alert_code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    count_label: Mapped[str] = mapped_column(String(30), nullable=False)
    count_bg: Mapped[str] = mapped_column(String(20), nullable=False)
    count_color: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    foot_note: Mapped[str] = mapped_column(String(120), nullable=False)
    action_to: Mapped[str] = mapped_column(String(120), nullable=False)
    display_order: Mapped[int] = mapped_column(nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DashboardInsight(Base):
    __tablename__ = "dashboard_insights"
    __table_args__ = (UniqueConstraint("section", "display_order", name="uq_dashboard_insight_order"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    section: Mapped[str] = mapped_column(String(30), nullable=False)
    display_order: Mapped[int] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text)
    note_color: Mapped[str | None] = mapped_column(String(20))
    tag: Mapped[str] = mapped_column(String(80), nullable=False)
    tag_bg: Mapped[str] = mapped_column(String(20), nullable=False)
    tag_color: Mapped[str] = mapped_column(String(20), nullable=False)


class SystemDashboardSnapshot(Base):
    __tablename__ = "system_dashboard_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    snapshot_code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)