from app.domains.pipeline.model import ProcessingStepCode, ProcessingStepStatus
from app.domains.pipeline.processing_steps import (
    PROCESSING_STEP_ORDER,
    initial_processing_steps_snapshot,
    processing_step_message,
    processing_step_progress,
)


def test_processing_step_order_and_initial_snapshot():
    assert [code.value for code in PROCESSING_STEP_ORDER] == [
        "DEDUPLICATION_PLAN",
        "MISSING_VALUE_PLAN",
        "DERIVED_COLUMN_ORDER",
        "FINAL_COLUMN_VALIDATION",
    ]
    snapshot = initial_processing_steps_snapshot()
    assert list(snapshot) == [code.value for code in PROCESSING_STEP_ORDER]
    assert {item["status"] for item in snapshot.values()} == {"PENDING"}


def test_processing_progress_is_monotonic_inside_processing_range():
    values = [
        processing_step_progress(code, status)
        for code in PROCESSING_STEP_ORDER
        for status in (ProcessingStepStatus.RUNNING, ProcessingStepStatus.COMPLETED)
    ]
    assert values == sorted(values)
    assert values[0] == 67
    assert values[-1] == 94


def test_processing_messages_are_fixed_safe_contracts():
    assert processing_step_message(
        ProcessingStepCode.MISSING_VALUE_PLAN, ProcessingStepStatus.RUNNING
    ) == "결측 처리 계획을 수립하고 있습니다."
