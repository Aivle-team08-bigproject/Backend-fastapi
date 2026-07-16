import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LoginSession(Base):
    __tablename__ = "login_sessions"
    __table_args__ = (
        Index("idx_session_employee", "employee_id"),
        Index("idx_session_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), nullable=False)

    # 원문은 저장하지 않고 SHA-256 해시만 저장한다 (RefreshTokenService.hash 대응)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    remember_me: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
    DateTime(),
    nullable=False,
)

    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    def is_active(self, now: datetime) -> bool:
        return self.revoked_at is None and self.expires_at > now
