import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.common.time_utils import utcnow
from app.db.base import Base


class AnalysisStatus(str, enum.Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class RequirementsAnalysisRun(Base):
    """요구사항 분석 에이전트 1회 실행 기록.

    사람이 나중에 raw_request(원본 자연어 요청)와 stage_prompts(에이전트가 생성한, 다음 단계
    에이전트들이 쓸 프롬프트)를 나란히 비교 검토할 수 있도록 실행 결과를 그대로 보존한다.
    """

    __tablename__ = "requirements_analysis_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    raw_request: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[AnalysisStatus] = mapped_column(Enum(AnalysisStatus), nullable=False, index=True)
    model_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    # {"data_selection_prompt": ..., "data_processing_prompt": ..., "qa_prompt": ...}
    stage_prompts: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
