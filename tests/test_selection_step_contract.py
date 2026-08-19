from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.domains.pipeline.model import SelectionStepCode, SelectionStepStatus
from app.domains.pipeline.selection_steps import (
    SELECTION_STEP_ORDER,
    SelectionStepSnapshot,
    SelectionStepsSnapshot,
    initial_selection_steps_snapshot,
    selection_step_message,
    selection_step_progress,
)
from agent_runtime.data_selection.agent import (
    _normalize_filter_columns,
    _normalize_privacy_selection,
)


NOW = datetime.now(timezone.utc)


def test_selection_step_order_and_initial_snapshot_are_stable():
    assert [step.value for step in SELECTION_STEP_ORDER] == [
        "SOURCE_COLUMN_SELECTION",
        "DERIVED_COLUMN_DESIGN",
        "SYNTHETIC_SAMPLE_GENERATION",
    ]

    snapshot = initial_selection_steps_snapshot()

    assert list(snapshot) == [step.value for step in SELECTION_STEP_ORDER]
    assert {item["status"] for item in snapshot.values()} == {"PENDING"}


def test_selection_step_progress_stays_inside_data_selection_range():
    assert selection_step_progress(
        SelectionStepCode.SOURCE_COLUMN_SELECTION,
        SelectionStepStatus.RUNNING,
    ) == 34
    assert selection_step_progress(
        SelectionStepCode.DERIVED_COLUMN_DESIGN,
        SelectionStepStatus.FAILED,
    ) == 45
    assert selection_step_progress(
        SelectionStepCode.SYNTHETIC_SAMPLE_GENERATION,
        SelectionStepStatus.COMPLETED,
    ) == 66


def test_selection_step_messages_are_safe_fixed_contracts():
    assert selection_step_message(
        SelectionStepCode.DERIVED_COLUMN_DESIGN,
        SelectionStepStatus.RUNNING,
    ) == "파생 컬럼을 정의하고 있습니다."
    assert selection_step_message(
        SelectionStepCode.SYNTHETIC_SAMPLE_GENERATION,
        SelectionStepStatus.FAILED,
    ) == "합성 샘플 생성에 실패했습니다."


def test_snapshot_rejects_later_step_before_predecessor_completion():
    with pytest.raises(ValidationError, match="started before its predecessor completed"):
        SelectionStepsSnapshot(
            root={
                SelectionStepCode.SOURCE_COLUMN_SELECTION: SelectionStepSnapshot(
                    status=SelectionStepStatus.PENDING
                ),
                SelectionStepCode.DERIVED_COLUMN_DESIGN: SelectionStepSnapshot(
                    status=SelectionStepStatus.RUNNING,
                    started_at=NOW,
                ),
                SelectionStepCode.SYNTHETIC_SAMPLE_GENERATION: SelectionStepSnapshot(
                    status=SelectionStepStatus.PENDING
                ),
            }
        )


def test_snapshot_rejects_missing_step_and_invalid_status_fields():
    with pytest.raises(ValidationError, match="every defined step"):
        SelectionStepsSnapshot(
            root={
                SelectionStepCode.SOURCE_COLUMN_SELECTION: SelectionStepSnapshot(
                    status=SelectionStepStatus.PENDING
                )
            }
        )

    with pytest.raises(ValidationError, match="requires error_message"):
        SelectionStepSnapshot(status=SelectionStepStatus.FAILED)


def test_privacy_normalizer_removes_identifiers_and_filters_from_high_risk_combo():
    result = {
        "source_columns": [
            {"column": "gender"},
            {"column": "age_band"},
            {"column": "transaction_datetime"},
        ],
        "selection_query": {
            "filters": {
                "transaction_datetime": {
                    "operator": "gte",
                    "value": "2026-08-01T00:00:00+09:00",
                }
            }
        },
        "interpretations": [],
    }

    _normalize_privacy_selection(result)

    assert [item["column"] for item in result["source_columns"]] == [
        "gender",
        "age_band",
    ]
    assert result["selection_query"]["filters"] == {}
    assert result["interpretations"][0]["term"] == "transaction_datetime"
    assert result["interpretations"][0]["requires_confirmation"] is True


def test_filter_normalizer_adds_missing_source_column_from_metadata():
    result = {
        "selected_tables": [{"table": "anon_transactions"}],
        "source_columns": [{"dataset": "anon_transactions", "column": "gender"}],
        "selection_query": {"filters": {"mcc_code": {"operator": "eq", "value": 5411}}},
    }
    metadata = [{
        "dataset": "anon_transactions",
        "columns": [
            {"name": "mcc_code", "data_type": "integer", "comment": "업종 코드"},
        ],
    }]

    _normalize_filter_columns(result, metadata)

    assert {item["column"] for item in result["source_columns"]} == {"gender", "mcc_code"}
