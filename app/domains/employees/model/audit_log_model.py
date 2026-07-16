from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AdminAuditLog(Base):
    """관리자(직원 관리 권한을 가진 사용자)의 민감한 조작을 기록하는 감사 로그.

    직원 생성, 권한 변경, 계정 상태 변경, 비밀번호 초기화, 세션 강제종료 등
    "누가 누구에게 무엇을 했는지"가 남아야 하는 조작들을 여기에 남긴다.
    """

    __tablename__ = "admin_audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    actor_employee_code: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    target_employee_code: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
