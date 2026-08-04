"""데이터 선별 Agent 내부 단계의 공통 상태·진행률·snapshot 계약."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, RootModel, model_validator

from app.domains.pipeline.model import SelectionStepCode, SelectionStepStatus


SELECTION_STEP_ORDER: tuple[SelectionStepCode, ...] = (
    SelectionStepCode.SOURCE_COLUMN_SELECTION,
    SelectionStepCode.DERIVED_COLUMN_DESIGN,
    SelectionStepCode.SYNTHETIC_SAMPLE_GENERATION,
)

SELECTION_STEP_PROGRESS: dict[
    tuple[SelectionStepCode, SelectionStepStatus], int
] = {
    (SelectionStepCode.SOURCE_COLUMN_SELECTION, SelectionStepStatus.RUNNING): 34,
    (SelectionStepCode.SOURCE_COLUMN_SELECTION, SelectionStepStatus.COMPLETED): 44,
    (SelectionStepCode.DERIVED_COLUMN_DESIGN, SelectionStepStatus.RUNNING): 45,
    (SelectionStepCode.DERIVED_COLUMN_DESIGN, SelectionStepStatus.COMPLETED): 55,
    (SelectionStepCode.SYNTHETIC_SAMPLE_GENERATION, SelectionStepStatus.RUNNING): 56,
    (SelectionStepCode.SYNTHETIC_SAMPLE_GENERATION, SelectionStepStatus.COMPLETED): 66,
}

SELECTION_STEP_MESSAGES: dict[
    tuple[SelectionStepCode, SelectionStepStatus], str
] = {
    (SelectionStepCode.SOURCE_COLUMN_SELECTION, SelectionStepStatus.PENDING):
        "원본 컬럼 선별을 기다리고 있습니다.",
    (SelectionStepCode.SOURCE_COLUMN_SELECTION, SelectionStepStatus.RUNNING):
        "원본 컬럼을 선별하고 있습니다.",
    (SelectionStepCode.SOURCE_COLUMN_SELECTION, SelectionStepStatus.COMPLETED):
        "원본 컬럼 선별이 완료되었습니다.",
    (SelectionStepCode.SOURCE_COLUMN_SELECTION, SelectionStepStatus.FAILED):
        "원본 컬럼 선별에 실패했습니다.",
    (SelectionStepCode.DERIVED_COLUMN_DESIGN, SelectionStepStatus.PENDING):
        "파생 컬럼 정의를 기다리고 있습니다.",
    (SelectionStepCode.DERIVED_COLUMN_DESIGN, SelectionStepStatus.RUNNING):
        "파생 컬럼을 정의하고 있습니다.",
    (SelectionStepCode.DERIVED_COLUMN_DESIGN, SelectionStepStatus.COMPLETED):
        "파생 컬럼 정의가 완료되었습니다.",
    (SelectionStepCode.DERIVED_COLUMN_DESIGN, SelectionStepStatus.FAILED):
        "파생 컬럼 정의에 실패했습니다.",
    (SelectionStepCode.SYNTHETIC_SAMPLE_GENERATION, SelectionStepStatus.PENDING):
        "합성 샘플 생성을 기다리고 있습니다.",
    (SelectionStepCode.SYNTHETIC_SAMPLE_GENERATION, SelectionStepStatus.RUNNING):
        "합성 샘플 5건을 생성하고 있습니다.",
    (SelectionStepCode.SYNTHETIC_SAMPLE_GENERATION, SelectionStepStatus.COMPLETED):
        "합성 샘플 5건이 준비되었습니다.",
    (SelectionStepCode.SYNTHETIC_SAMPLE_GENERATION, SelectionStepStatus.FAILED):
        "합성 샘플 생성에 실패했습니다.",
}


class SelectionStepSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SelectionStepStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    metadata: dict | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def validate_status_fields(self) -> "SelectionStepSnapshot":
        if self.status == SelectionStepStatus.RUNNING and self.started_at is None:
            raise ValueError("RUNNING selection step requires started_at")
        if self.status == SelectionStepStatus.COMPLETED and (
            self.started_at is None or self.completed_at is None
        ):
            raise ValueError("COMPLETED selection step requires started_at and completed_at")
        if self.status == SelectionStepStatus.FAILED and not self.error_message:
            raise ValueError("FAILED selection step requires error_message")
        return self


class SelectionStepsSnapshot(
    RootModel[dict[SelectionStepCode, SelectionStepSnapshot]]
):
    @model_validator(mode="after")
    def validate_steps(self) -> "SelectionStepsSnapshot":
        if set(self.root) != set(SELECTION_STEP_ORDER):
            raise ValueError("selection snapshot must contain every defined step exactly once")

        previous_completed = True
        for code in SELECTION_STEP_ORDER:
            status = self.root[code].status
            if status in {
                SelectionStepStatus.RUNNING,
                SelectionStepStatus.COMPLETED,
                SelectionStepStatus.FAILED,
            } and not previous_completed:
                raise ValueError(f"selection step {code.value} started before its predecessor completed")
            previous_completed = status == SelectionStepStatus.COMPLETED
        return self


def initial_selection_steps_snapshot() -> dict:
    snapshot = SelectionStepsSnapshot(
        root={
            code: SelectionStepSnapshot(status=SelectionStepStatus.PENDING)
            for code in SELECTION_STEP_ORDER
        }
    )
    return snapshot.model_dump(mode="json")


def selection_step_progress(
    step: SelectionStepCode,
    status: SelectionStepStatus,
) -> int:
    if status == SelectionStepStatus.FAILED:
        return SELECTION_STEP_PROGRESS[(step, SelectionStepStatus.RUNNING)]
    return SELECTION_STEP_PROGRESS[(step, status)]


def selection_step_message(
    step: SelectionStepCode,
    status: SelectionStepStatus,
) -> str:
    return SELECTION_STEP_MESSAGES[(step, status)]
