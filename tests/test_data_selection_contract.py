import pytest

from agent_runtime.data_selection import agent as selection_agent
from agent_runtime.data_selection.agent import _validate_contract


SCHEMA_METADATA = [
    {
        "dataset": "transaction_pseudonymized",
        "schema": "anonymized",
        "table": "transactions",
        "comment": "가명 거래",
        "columns": [
            {
                "name": "transaction_id",
                "data_type": "character varying",
                "comment": "거래 식별자",
            },
            {
                "name": "krw_converted_amount",
                "data_type": "numeric",
                "comment": "원화 환산 금액",
            },
        ],
    }
]


def _selection():
    return {
        "selected_tables": [
            {"table": "transaction_pseudonymized", "reason": "결제 분석"}
        ],
        "source_columns": [
            {
                "dataset": "transaction_pseudonymized",
                "column": "transaction_id",
                "data_type": "character varying",
                "comment": "거래 식별자",
                "reason": "건수 계산",
            },
            {
                "dataset": "transaction_pseudonymized",
                "column": "krw_converted_amount",
                "data_type": "numeric",
                "comment": "원화 환산 금액",
                "reason": "금액 계산",
            },
        ],
        "derived_columns": [
            {
                "name": "고객결제금액",
                "data_type": "number",
                "source_columns": ["krw_converted_amount"],
                "derivation": "원화 환산 금액 합계",
                "description": "고객 결제 금액",
            }
        ],
        "selection_query": {
            "columns": ["transaction_id", "krw_converted_amount"],
            "filters": {},
        },
        "sample_columns": [
            {
                "name": "고객결제금액",
                "data_type": "number",
                "is_derived": True,
                "source_columns": ["krw_converted_amount"],
                "description": "고객 결제 금액",
            }
        ],
        "sample_rows": [{"고객결제금액": value} for value in range(1, 6)],
        "sample_metadata": {"is_synthetic": True, "sample_count": 5},
    }


def test_column_design_contract_accepts_comment_based_sources_and_derived_columns():
    _validate_contract(_selection(), SCHEMA_METADATA)


def test_column_design_contract_rejects_top_k_and_vector_similarity():
    selection = _selection()
    selection["selection_query"]["top_k"] = 20

    with pytest.raises(ValueError, match="조회량 또는 벡터 검색"):
        _validate_contract(selection, SCHEMA_METADATA)


def test_column_design_contract_rejects_unknown_db_column():
    selection = _selection()
    selection["source_columns"][0]["column"] = "invented_column"

    with pytest.raises(ValueError, match="DB 메타데이터에 없는"):
        _validate_contract(selection, SCHEMA_METADATA)


def test_column_design_contract_rejects_unselected_derived_source():
    selection = _selection()
    selection["derived_columns"][0]["source_columns"] = ["transaction_amount"]

    with pytest.raises(ValueError, match="선택된 source column"):
        _validate_contract(selection, SCHEMA_METADATA)


def test_column_design_contract_rejects_empty_derived_columns():
    selection = _selection()
    selection["derived_columns"] = []

    with pytest.raises(ValueError, match="최소 1개"):
        _validate_contract(selection, SCHEMA_METADATA)


def test_run_retries_empty_derived_columns_three_times_then_fails(monkeypatch):
    selection = _selection()
    selection["derived_columns"] = []
    messages = []

    class StubAgent:
        def __call__(self, message):
            messages.append(message)
            return __import__("json").dumps(selection, ensure_ascii=False)

    monkeypatch.setattr(selection_agent, "build_agent", lambda: StubAgent())

    result = selection_agent.run(
        "고객별 결제 성향을 분석해줘",
        {"requested_data_sentence": "고객별 결제 성향 분석"},
        ["transaction_pseudonymized"],
        SCHEMA_METADATA,
    )

    assert result["ok"] is False
    assert len(messages) == 3
    assert "3회 시도 후에도 실패" in result["error_message"]
    assert "derived_columns는 최소 1개" in result["error_message"]
    assert '"retry_feedback": "직전 1회차 결과 검증 실패' in messages[1]
