"""portfolio DB, service.requirements 테이블 매핑 (V7__service_schema.sql 기준).

app_svc 커넥션(app/db/portfolio_app_session.py)의 Base를 사용한다.
client_id/owner_id는 service.clients/service.users를 FK로 참조하지만,
이번 단계에선 두 테이블에 대한 ORM 모델까지는 만들지 않는다(참조만 걸어둠,
조인이 필요해지면 그때 최소 모델을 추가).
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.portfolio_app_session import Base
from app.domains.requirements.model.enums import ProvideFormat, RequirementStatus


class Requirement(Base):
    __tablename__ = "requirements"
    __table_args__ = {"schema": "service"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    request_no: Mapped[str] = mapped_column(String(30), unique=True)

    client_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("service.clients.id")
    )
    requester_name: Mapped[str | None] = mapped_column(String(100))

    owner_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("service.users.id"))
    owner_name: Mapped[str | None] = mapped_column(String(100))

    business_purpose: Mapped[str | None] = mapped_column(Text)
    processing_request: Mapped[str] = mapped_column(Text)

    provide_format: Mapped[ProvideFormat] = mapped_column(String(20))
    usage_period: Mapped[str | None] = mapped_column(String(100))
    analysis_condition: Mapped[str | None] = mapped_column(Text)
    sample_email: Mapped[str | None] = mapped_column(String(200))

    status: Mapped[RequirementStatus] = mapped_column(
        String(30), default=RequirementStatus.RECEIVED
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())