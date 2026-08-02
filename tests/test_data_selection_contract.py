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
        "interpretations": [],
        "catalog_issues": [],
        "catalog_matches": [],
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


def test_column_design_contract_accepts_comment_based_filter_and_interpretation():
    selection = _selection()
    selection["selection_query"]["filters"] = {
        "krw_converted_amount": {
            "operator": "gte",
            "value": 10000,
            "reason": "사용자가 일정 금액 이상의 결제를 요청함",
            "evidence": "원화 환산 금액이라는 컬럼 COMMENT",
        }
    }
    selection["interpretations"] = [
        {
            "term": "고액 결제",
            "interpreted_as": "원화 환산 금액 1만원 이상",
            "reason": "요청 문맥과 컬럼 COMMENT를 종합한 가장 가까운 의미",
            "requires_confirmation": True,
        }
    ]

    _validate_contract(selection, SCHEMA_METADATA)


def test_column_design_contract_rejects_filter_without_comment_evidence():
    selection = _selection()
    selection["selection_query"]["filters"] = {
        "krw_converted_amount": {
            "operator": "gte",
            "value": 10000,
            "reason": "고액 결제 조건",
            "evidence": "",
        }
    }

    with pytest.raises(ValueError, match="COMMENT 기반 evidence"):
        _validate_contract(selection, SCHEMA_METADATA)


def test_column_design_contract_rejects_filter_for_unselected_column():
    selection = _selection()
    selection["selection_query"]["filters"] = {
        "age_band": {
            "operator": "eq",
            "value": "30대",
            "reason": "30대 요청",
            "evidence": "연령대 COMMENT",
        }
    }

    with pytest.raises(ValueError, match="선택된 source column"):
        _validate_contract(selection, SCHEMA_METADATA)


def test_column_design_contract_rejects_between_for_text_column():
    selection = _selection()
    selection["selection_query"]["filters"] = {
        "transaction_id": {
            "operator": "between",
            "value": ["A", "Z"],
            "reason": "문자열 범위 요청",
            "evidence": "거래 식별자 COMMENT",
        }
    }

    with pytest.raises(ValueError, match="문자열 범위"):
        _validate_contract(selection, SCHEMA_METADATA)


def test_column_design_contract_accepts_catalog_issue_instead_of_guessing():
    selection = _selection()
    selection["catalog_issues"] = [
        {
            "term": "여행 업종",
            "reason": "COMMENT에 실행 가능한 MCC 값이 정의되지 않음",
            "required_information": "여행 업종에 해당하는 MCC 기준",
        }
    ]

    _validate_contract(selection, SCHEMA_METADATA)


def test_column_design_contract_accepts_real_mcc_catalog_matches():
    catalogs = [
        {
            "catalog": "mcc_codes",
            "entries": [
                {"mcc_code": 4722, "mcc_name": "여행사"},
                {"mcc_code": 7011, "mcc_name": "호텔/숙박"},
            ],
        }
    ]
    metadata = [
        {
            **SCHEMA_METADATA[0],
            "columns": [
                *SCHEMA_METADATA[0]["columns"],
                {"name": "mcc_code", "data_type": "integer", "comment": "거래 업종 코드"},
            ],
        }
    ]
    selection = _selection()
    selection["source_columns"].append(
        {
            "dataset": "transaction_pseudonymized",
            "column": "mcc_code",
            "data_type": "integer",
            "comment": "거래 업종 코드",
            "reason": "여행 업종 선별",
        }
    )
    selection["selection_query"]["columns"].append("mcc_code")
    selection["selection_query"]["filters"] = {
        "mcc_code": {
            "operator": "in",
            "value": [4722, 7011],
            "reason": "여행 업종 요청",
            "evidence": "MCC 카탈로그의 여행사와 호텔/숙박",
        }
    }
    selection["catalog_matches"] = [
        {
            "term": "여행 업종",
            "catalog": "mcc_codes",
            "matches": [
                {"code": 4722, "label": "여행사"},
                {"code": 7011, "label": "호텔/숙박"},
            ],
            "reason": "카탈로그에서 의미적으로 가장 가까운 업종",
            "requires_confirmation": True,
        }
    ]

    _validate_contract(selection, metadata, catalogs)


def test_column_design_contract_rejects_unknown_mcc_catalog_code():
    catalogs = [
        {
            "catalog": "mcc_codes",
            "entries": [{"mcc_code": 4722, "mcc_name": "여행사"}],
        }
    ]
    metadata = [
        {
            **SCHEMA_METADATA[0],
            "columns": [
                *SCHEMA_METADATA[0]["columns"],
                {"name": "mcc_code", "data_type": "integer", "comment": "거래 업종 코드"},
            ],
        }
    ]
    selection = _selection()
    selection["source_columns"].append(
        {
            "dataset": "transaction_pseudonymized",
            "column": "mcc_code",
            "data_type": "integer",
            "comment": "거래 업종 코드",
            "reason": "여행 업종 선별",
        }
    )
    selection["selection_query"]["columns"].append("mcc_code")
    selection["selection_query"]["filters"] = {
        "mcc_code": {
            "operator": "eq",
            "value": 9999,
            "reason": "여행 업종 요청",
            "evidence": "잘못 생성한 코드",
        }
    }

    with pytest.raises(ValueError, match="실제 MCC 카탈로그 코드"):
        _validate_contract(selection, metadata, catalogs)


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


def test_run_passes_hitl_feedback_to_model(monkeypatch):
    selection = _selection()
    messages = []

    class StubAgent:
        def __call__(self, message):
            messages.append(message)
            return __import__("json").dumps(selection, ensure_ascii=False)

    monkeypatch.setattr(selection_agent, "build_agent", lambda: StubAgent())

    result = selection_agent.run(
        "수도권 결제를 분석해줘",
        {"requested_data_sentence": "수도권 결제 분석"},
        ["transaction_pseudonymized"],
        SCHEMA_METADATA,
        "수도권은 서울, 경기, 인천을 의미합니다.",
    )

    assert result["ok"] is True
    assert '"hitl_feedback": "수도권은 서울, 경기, 인천을 의미합니다."' in messages[0]
