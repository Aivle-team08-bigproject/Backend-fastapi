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
    """요구사항 분석 에이전트 1회 실행 기록 (이 에이전트 단독 테스트용).

    사람이 나중에 raw_request(원본 자연어 요청)와 analysis_result(에이전트가 구조화한
    사용목적/요구데이터/카테고리/전달매체/가공형태)를 나란히 비교 검토할 수 있도록 실행 결과를
    그대로 보존한다.

    status는 이 에이전트 자신의 실행 성공/실패만 나타낸다(모델 호출·응답 파싱이 됐는지) —
    산출물 내용이 파이프라인 규격에 맞는지, 다음 단계로 넘겨도 되는지에 대한 판단은 담지 않는다.
    그 판단은 오케스트레이션(다른 담당자가 별도 구현, 이후 병합 예정)의 몫이다.
    """

    __tablename__ = "requirements_analysis_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    raw_request: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[AnalysisStatus] = mapped_column(Enum(AnalysisStatus), nullable=False, index=True)
    model_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)
    # {"usage_purpose", "requested_data_summary", "requested_data_categories",
    #  "delivery_channel", "output_format"}
    analysis_result: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
