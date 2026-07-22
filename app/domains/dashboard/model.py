from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.common.time_utils import utcnow
from app.db.base import Base


class TaskViewSnapshot(Base):
    """에이전트/파이프라인 결과를 화면 단계별로 읽기 좋게 만든 영속 조회 스냅샷."""

    __tablename__ = "task_view_snapshots"
    __table_args__ = (UniqueConstraint("data_request_id", "view_code", name="uq_task_view_snapshot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_request_id: Mapped[int] = mapped_column(ForeignKey("data_requests.id"), nullable=False, index=True)
    view_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utcnow, onupdate=utcnow)


class DashboardAlert(Base):
    __tablename__ = "dashboard_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_code: Mapped[str] = mapped_column(String(60), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    count_label: Mapped[str] = mapped_column(String(30), nullable=False)
    count_bg: Mapped[str] = mapped_column(String(20), nullable=False)
    count_color: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    foot_note: Mapped[str] = mapped_column(String(120), nullable=False)
    action_to: Mapped[str] = mapped_column(String(120), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utcnow, onupdate=utcnow)


class DashboardInsight(Base):
    __tablename__ = "dashboard_insights"
    __table_args__ = (UniqueConstraint("section", "display_order", name="uq_dashboard_insight_order"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    section: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text)
    note_color: Mapped[str | None] = mapped_column(String(20))
    tag: Mapped[str] = mapped_column(String(80), nullable=False)
    tag_bg: Mapped[str] = mapped_column(String(20), nullable=False)
    tag_color: Mapped[str] = mapped_column(String(20), nullable=False)


class SystemDashboardSnapshot(Base):
    """모니터링 시스템이 주기적으로 갱신하는 개발자 대시보드 read model."""

    __tablename__ = "system_dashboard_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_code: Mapped[str] = mapped_column(String(60), nullable=False, unique=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utcnow, onupdate=utcnow)
