"""요구사항 분석 Agent 내부 단계의 공통 상태·진행률·snapshot 계약.

app/domains/pipeline/selection_steps.py와 동일한 패턴이다 — 세 Agent(요구사항 분석/데이터
선별/데이터 가공)가 같은 방식으로 서브스텝을 추적하도록 통일한다.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, RootModel, model_validator

from app.domains.pipeline.model import AnalysisStepCode, AnalysisStepStatus


ANALYSIS_STEP_ORDER: tuple[AnalysisStepCode, ...] = (
    AnalysisStepCode.REQUEST_ANALYSIS,
    AnalysisStepCode.REQUEST_STRUCTURING,
    AnalysisStepCode.DATA_CATEGORIZATION,
)

ANALYSIS_STEP_PROGRESS: dict[
    tuple[AnalysisStepCode, AnalysisStepStatus], int
] = {
    (AnalysisStepCode.REQUEST_ANALYSIS, AnalysisStepStatus.RUNNING): 1,
    (AnalysisStepCode.REQUEST_ANALYSIS, AnalysisStepStatus.COMPLETED): 11,
    (AnalysisStepCode.REQUEST_STRUCTURING, AnalysisStepStatus.RUNNING): 12,
    (AnalysisStepCode.REQUEST_STRUCTURING, AnalysisStepStatus.COMPLETED): 22,
    (AnalysisStepCode.DATA_CATEGORIZATION, AnalysisStepStatus.RUNNING): 23,
    (AnalysisStepCode.DATA_CATEGORIZATION, AnalysisStepStatus.COMPLETED): 33,
}

ANALYSIS_STEP_MESSAGES: dict[
    tuple[AnalysisStepCode, AnalysisStepStatus], str
] = {
    (AnalysisStepCode.REQUEST_ANALYSIS, AnalysisStepStatus.PENDING):
        "요청 분석을 기다리고 있습니다.",
    (AnalysisStepCode.REQUEST_ANALYSIS, AnalysisStepStatus.RUNNING):
        "요청을 분석하고 있습니다.",
    (AnalysisStepCode.REQUEST_ANALYSIS, AnalysisStepStatus.COMPLETED):
        "요청 분석이 완료되었습니다.",
    (AnalysisStepCode.REQUEST_ANALYSIS, AnalysisStepStatus.FAILED):
        "요청 분석에 실패했습니다.",
    (AnalysisStepCode.REQUEST_STRUCTURING, AnalysisStepStatus.PENDING):
        "요청 구조화를 기다리고 있습니다.",
    (AnalysisStepCode.REQUEST_STRUCTURING, AnalysisStepStatus.RUNNING):
        "요청을 구조화하고 있습니다.",
    (AnalysisStepCode.REQUEST_STRUCTURING, AnalysisStepStatus.COMPLETED):
        "요청 구조화가 완료되었습니다.",
    (AnalysisStepCode.REQUEST_STRUCTURING, AnalysisStepStatus.FAILED):
        "요청 구조화에 실패했습니다.",
    (AnalysisStepCode.DATA_CATEGORIZATION, AnalysisStepStatus.PENDING):
        "데이터 범주화를 기다리고 있습니다.",
    (AnalysisStepCode.DATA_CATEGORIZATION, AnalysisStepStatus.RUNNING):
        "데이터를 범주화하고 있습니다.",
    (AnalysisStepCode.DATA_CATEGORIZATION, AnalysisStepStatus.COMPLETED):
        "데이터 범주화가 완료되었습니다.",
    (AnalysisStepCode.DATA_CATEGORIZATION, AnalysisStepStatus.FAILED):
        "데이터 범주화에 실패했습니다.",
}


class AnalysisStepSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AnalysisStepStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    metadata: dict | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def validate_status_fields(self) -> "AnalysisStepSnapshot":
        if self.status == AnalysisStepStatus.RUNNING and self.started_at is None:
            raise ValueError("RUNNING analysis step requires started_at")
        if self.status == AnalysisStepStatus.COMPLETED and (
            self.started_at is None or self.completed_at is None
        ):
            raise ValueError("COMPLETED analysis step requires started_at and completed_at")
        if self.status == AnalysisStepStatus.FAILED and not self.error_message:
            raise ValueError("FAILED analysis step requires error_message")
        return self


class AnalysisStepsSnapshot(
    RootModel[dict[AnalysisStepCode, AnalysisStepSnapshot]]
):
    @model_validator(mode="after")
    def validate_steps(self) -> "AnalysisStepsSnapshot":
        if set(self.root) != set(ANALYSIS_STEP_ORDER):
            raise ValueError("analysis snapshot must contain every defined step exactly once")

        previous_completed = True
        for code in ANALYSIS_STEP_ORDER:
            status = self.root[code].status
            if status in {
                AnalysisStepStatus.RUNNING,
                AnalysisStepStatus.COMPLETED,
                AnalysisStepStatus.FAILED,
            } and not previous_completed:
                raise ValueError(f"analysis step {code.value} started before its predecessor completed")
            previous_completed = status == AnalysisStepStatus.COMPLETED
        return self


def initial_analysis_steps_snapshot() -> dict:
    snapshot = AnalysisStepsSnapshot(
        root={
            code: AnalysisStepSnapshot(status=AnalysisStepStatus.PENDING)
            for code in ANALYSIS_STEP_ORDER
        }
    )
    return snapshot.model_dump(mode="json")


def analysis_step_progress(
    step: AnalysisStepCode,
    status: AnalysisStepStatus,
) -> int:
    if status == AnalysisStepStatus.FAILED:
        return ANALYSIS_STEP_PROGRESS[(step, AnalysisStepStatus.RUNNING)]
    return ANALYSIS_STEP_PROGRESS[(step, status)]


def analysis_step_message(
    step: AnalysisStepCode,
    status: AnalysisStepStatus,
) -> str:
    return ANALYSIS_STEP_MESSAGES[(step, status)]
