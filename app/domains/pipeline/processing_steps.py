"""데이터 가공 Agent의 4단계 상태·진행률·snapshot 계약."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.domains.pipeline.model import ProcessingStepCode, ProcessingStepStatus


PROCESSING_STEP_ORDER = (
    ProcessingStepCode.DEDUPLICATION_PLAN,
    ProcessingStepCode.MISSING_VALUE_PLAN,
    ProcessingStepCode.DERIVED_COLUMN_ORDER,
    ProcessingStepCode.FINAL_COLUMN_VALIDATION,
)

PROCESSING_STEP_PROGRESS = {
    (ProcessingStepCode.DEDUPLICATION_PLAN, ProcessingStepStatus.RUNNING): 67,
    (ProcessingStepCode.DEDUPLICATION_PLAN, ProcessingStepStatus.COMPLETED): 73,
    (ProcessingStepCode.MISSING_VALUE_PLAN, ProcessingStepStatus.RUNNING): 74,
    (ProcessingStepCode.MISSING_VALUE_PLAN, ProcessingStepStatus.COMPLETED): 80,
    (ProcessingStepCode.DERIVED_COLUMN_ORDER, ProcessingStepStatus.RUNNING): 81,
    (ProcessingStepCode.DERIVED_COLUMN_ORDER, ProcessingStepStatus.COMPLETED): 87,
    (ProcessingStepCode.FINAL_COLUMN_VALIDATION, ProcessingStepStatus.RUNNING): 88,
    (ProcessingStepCode.FINAL_COLUMN_VALIDATION, ProcessingStepStatus.COMPLETED): 94,
}

PROCESSING_STEP_MESSAGES = {
    (ProcessingStepCode.DEDUPLICATION_PLAN, ProcessingStepStatus.PENDING): "중복 제거 계획을 기다리고 있습니다.",
    (ProcessingStepCode.DEDUPLICATION_PLAN, ProcessingStepStatus.RUNNING): "중복 제거 계획을 수립하고 있습니다.",
    (ProcessingStepCode.DEDUPLICATION_PLAN, ProcessingStepStatus.COMPLETED): "중복 제거 계획이 완료되었습니다.",
    (ProcessingStepCode.DEDUPLICATION_PLAN, ProcessingStepStatus.FAILED): "중복 제거 계획 수립에 실패했습니다.",
    (ProcessingStepCode.MISSING_VALUE_PLAN, ProcessingStepStatus.PENDING): "결측 처리 계획을 기다리고 있습니다.",
    (ProcessingStepCode.MISSING_VALUE_PLAN, ProcessingStepStatus.RUNNING): "결측 처리 계획을 수립하고 있습니다.",
    (ProcessingStepCode.MISSING_VALUE_PLAN, ProcessingStepStatus.COMPLETED): "결측 처리 계획이 완료되었습니다.",
    (ProcessingStepCode.MISSING_VALUE_PLAN, ProcessingStepStatus.FAILED): "결측 처리 계획 수립에 실패했습니다.",
    (ProcessingStepCode.DERIVED_COLUMN_ORDER, ProcessingStepStatus.PENDING): "파생 컬럼 생성 순서 결정을 기다리고 있습니다.",
    (ProcessingStepCode.DERIVED_COLUMN_ORDER, ProcessingStepStatus.RUNNING): "파생 컬럼 생성 순서를 결정하고 있습니다.",
    (ProcessingStepCode.DERIVED_COLUMN_ORDER, ProcessingStepStatus.COMPLETED): "파생 컬럼 생성 순서가 결정되었습니다.",
    (ProcessingStepCode.DERIVED_COLUMN_ORDER, ProcessingStepStatus.FAILED): "파생 컬럼 생성 순서 결정에 실패했습니다.",
    (ProcessingStepCode.FINAL_COLUMN_VALIDATION, ProcessingStepStatus.PENDING): "최종 컬럼·품질 검증 정의를 기다리고 있습니다.",
    (ProcessingStepCode.FINAL_COLUMN_VALIDATION, ProcessingStepStatus.RUNNING): "최종 컬럼과 품질 검증을 정의하고 있습니다.",
    (ProcessingStepCode.FINAL_COLUMN_VALIDATION, ProcessingStepStatus.COMPLETED): "최종 컬럼·품질 검증 정의가 완료되었습니다.",
    (ProcessingStepCode.FINAL_COLUMN_VALIDATION, ProcessingStepStatus.FAILED): "최종 컬럼·품질 검증 정의에 실패했습니다.",
}


class ProcessingStepSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ProcessingStepStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    metadata: dict | None = None
    error_message: str | None = None


def initial_processing_steps_snapshot() -> dict:
    return {
        code.value: ProcessingStepSnapshot(
            status=ProcessingStepStatus.PENDING
        ).model_dump(mode="json")
        for code in PROCESSING_STEP_ORDER
    }


def processing_step_progress(
    step: ProcessingStepCode, status: ProcessingStepStatus
) -> int:
    if status == ProcessingStepStatus.FAILED:
        return PROCESSING_STEP_PROGRESS[(step, ProcessingStepStatus.RUNNING)]
    return PROCESSING_STEP_PROGRESS[(step, status)]


def processing_step_message(
    step: ProcessingStepCode, status: ProcessingStepStatus
) -> str:
    return PROCESSING_STEP_MESSAGES[(step, status)]
