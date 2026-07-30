from app.application.validation import validate_stage_output
from app.domain.enums import StageName


def _valid_selection() -> dict:
    return {
        "selected_tables": [
            {"table": "transaction_pseudonymized", "reason": "결제 데이터 조회"},
        ],
        "selection_query": {
            "vector_similarity": True,
            "top_k": 20,
            "filters": {"지역": "일본"},
        },
        "sample_columns": [
            {
                "name": "지역",
                "data_type": "string",
                "is_predicted": False,
                "description": "조회 지역",
            },
            {
                "name": "결제건수",
                "data_type": "integer",
                "is_predicted": True,
                "description": "형식 확인용 합성 결제 건수",
            },
        ],
        "sample_rows": [
            {"지역": "일본", "결제건수": index}
            for index in range(1, 6)
        ],
        "sample_metadata": {
            "is_synthetic": True,
            "sample_count": 5,
            "notice": "실제 고객 데이터가 아닌 형식 확인용 예시 데이터입니다.",
        },
    }


def test_data_selection_accepts_five_synthetic_rows():
    result = validate_stage_output(StageName.DATA_SELECTION, _valid_selection())

    assert result == {"passed": True, "errors": [], "failure_code": None}


def test_data_selection_rejects_wrong_sample_count():
    output = _valid_selection()
    output["sample_rows"] = output["sample_rows"][:4]

    result = validate_stage_output(StageName.DATA_SELECTION, output)

    assert result["passed"] is False
    assert "sample_rows must contain exactly 5 rows" in result["errors"]


def test_data_selection_rejects_sample_column_mismatch():
    output = _valid_selection()
    output["sample_rows"][0] = {"지역": "일본", "없는컬럼": 1}

    result = validate_stage_output(StageName.DATA_SELECTION, output)

    assert result["passed"] is False
    assert "sample_rows[0] columns must exactly match sample_columns" in result["errors"]


def test_data_selection_requires_synthetic_marker():
    output = _valid_selection()
    output["sample_metadata"]["is_synthetic"] = False

    result = validate_stage_output(StageName.DATA_SELECTION, output)

    assert result["passed"] is False
    assert "sample_metadata must identify exactly 5 synthetic rows" in result["errors"]
