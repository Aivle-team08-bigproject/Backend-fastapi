from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.domains.pipeline.model import AnalysisStepCode, AnalysisStepStatus
from app.domains.pipeline.analysis_steps import (
    ANALYSIS_STEP_ORDER,
    AnalysisStepSnapshot,
    AnalysisStepsSnapshot,
    initial_analysis_steps_snapshot,
    analysis_step_message,
    analysis_step_progress,
)


NOW = datetime.now(timezone.utc)


def test_analysis_step_order_and_initial_snapshot_are_stable():
    assert [step.value for step in ANALYSIS_STEP_ORDER] == [
        "REQUEST_ANALYSIS",
        "REQUEST_STRUCTURING",
        "DATA_CATEGORIZATION",
    ]

    snapshot = initial_analysis_steps_snapshot()

    assert list(snapshot) == [step.value for step in ANALYSIS_STEP_ORDER]
    assert {item["status"] for item in snapshot.values()} == {"PENDING"}


def test_analysis_step_progress_stays_inside_requirement_analysis_range():
    assert analysis_step_progress(
        AnalysisStepCode.REQUEST_ANALYSIS,
        AnalysisStepStatus.RUNNING,
    ) == 1
    assert analysis_step_progress(
        AnalysisStepCode.REQUEST_STRUCTURING,
        AnalysisStepStatus.FAILED,
    ) == 12
    assert analysis_step_progress(
        AnalysisStepCode.DATA_CATEGORIZATION,
        AnalysisStepStatus.COMPLETED,
    ) == 33


def test_analysis_step_messages_are_safe_fixed_contracts():
    assert analysis_step_message(
        AnalysisStepCode.REQUEST_STRUCTURING,
        AnalysisStepStatus.RUNNING,
    ) == "요청을 구조화하고 있습니다."
    assert analysis_step_message(
        AnalysisStepCode.DATA_CATEGORIZATION,
        AnalysisStepStatus.FAILED,
    ) == "데이터 범주화에 실패했습니다."


def test_snapshot_rejects_later_step_before_predecessor_completion():
    with pytest.raises(ValidationError, match="started before its predecessor completed"):
        AnalysisStepsSnapshot(
            root={
                AnalysisStepCode.REQUEST_ANALYSIS: AnalysisStepSnapshot(
                    status=AnalysisStepStatus.PENDING
                ),
                AnalysisStepCode.REQUEST_STRUCTURING: AnalysisStepSnapshot(
                    status=AnalysisStepStatus.RUNNING,
                    started_at=NOW,
                ),
                AnalysisStepCode.DATA_CATEGORIZATION: AnalysisStepSnapshot(
                    status=AnalysisStepStatus.PENDING
                ),
            }
        )


def test_snapshot_rejects_missing_step_and_invalid_status_fields():
    with pytest.raises(ValidationError, match="every defined step"):
        AnalysisStepsSnapshot(
            root={
                AnalysisStepCode.REQUEST_ANALYSIS: AnalysisStepSnapshot(
                    status=AnalysisStepStatus.PENDING
                )
            }
        )

    with pytest.raises(ValidationError, match="requires error_message"):
        AnalysisStepSnapshot(status=AnalysisStepStatus.FAILED)
