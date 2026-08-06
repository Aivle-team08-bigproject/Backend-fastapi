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
                "derivation_spec": {
                    "spec_version": "1.0",
                    "operation": "arithmetic",
                    "parameters": {
                        "operator": "add",
                        "operands": [
                            {"column": "krw_converted_amount"},
                            {"literal": 0},
                        ],
                    },
                    "evidence": "원화 환산 금액이라는 DB COMMENT와 고객 요청",
                },
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


def _source_result():
    selection = _selection()
    return {
        key: selection[key]
        for key in (
            "selected_tables",
            "source_columns",
            "selection_query",
            "interpretations",
            "catalog_issues",
            "catalog_matches",
        )
    }


def _derived_result():
    return {"derived_columns": _selection()["derived_columns"]}


def _sample_result():
    selection = _selection()
    return {
        key: selection[key]
        for key in ("sample_columns", "sample_rows", "sample_metadata")
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

    with pytest.raises(ValueError, match="원본 또는 앞에서 정의된 파생 컬럼"):
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
    messages: dict[str, list[str]] = {"source": [], "derived": [], "sample": []}

    class StubAgent:
        def __init__(self, step, result):
            self.step = step
            self.result = result

        def __call__(self, message):
            messages[self.step].append(message)
            return __import__("json").dumps(self.result, ensure_ascii=False)

    def build_agent(prompt):
        if prompt == selection_agent.SOURCE_COLUMN_SELECTION_PROMPT:
            return StubAgent("source", _source_result())
        if prompt == selection_agent.DERIVED_COLUMN_DESIGN_PROMPT:
            return StubAgent("derived", {"derived_columns": []})
        return StubAgent("sample", _sample_result())

    monkeypatch.setattr(selection_agent, "build_agent", build_agent)

    result = selection_agent.run(
        "고객별 결제 성향을 분석해줘",
        {"requested_data_sentence": "고객별 결제 성향 분석"},
        ["transaction_pseudonymized"],
        SCHEMA_METADATA,
    )

    assert result["ok"] is False
    assert len(messages["source"]) == 1
    assert len(messages["derived"]) == 3
    assert messages["sample"] == []
    assert "파생 컬럼 정의 단계가 3회 시도 후에도 실패" in result["error_message"]
    assert "derived_columns는 최소 1개" in result["error_message"]
    assert '"retry_feedback": "직전 1회차 파생 컬럼 정의 결과 검증 실패' in messages["derived"][1]


def test_run_passes_hitl_feedback_to_model(monkeypatch):
    messages = []

    class StubAgent:
        def __init__(self, result):
            self.result = result

        def __call__(self, message):
            messages.append(message)
            return __import__("json").dumps(self.result, ensure_ascii=False)

    def build_agent(prompt):
        if prompt == selection_agent.SOURCE_COLUMN_SELECTION_PROMPT:
            return StubAgent(_source_result())
        if prompt == selection_agent.DERIVED_COLUMN_DESIGN_PROMPT:
            return StubAgent(_derived_result())
        return StubAgent(_sample_result())

    monkeypatch.setattr(selection_agent, "build_agent", build_agent)

    result = selection_agent.run(
        "수도권 결제를 분석해줘",
        {"requested_data_sentence": "수도권 결제 분석"},
        ["transaction_pseudonymized"],
        SCHEMA_METADATA,
        "수도권은 서울, 경기, 인천을 의미합니다.",
    )

    assert result["ok"] is True
    assert len(messages) == 3
    assert all(
        '"hitl_feedback": "수도권은 서울, 경기, 인천을 의미합니다."' in message
        for message in messages
    )
    assert '"source_selection"' not in messages[0]
    assert '"source_selection"' in messages[1]
    assert '"derived_design"' in messages[2]


def test_run_steps_reports_ordered_statuses_and_small_summaries(monkeypatch):
    class StubAgent:
        def __init__(self, result):
            self.result = result

        def __call__(self, message):
            return __import__("json").dumps(self.result, ensure_ascii=False)

    def build_agent(prompt):
        if prompt == selection_agent.SOURCE_COLUMN_SELECTION_PROMPT:
            return StubAgent(_source_result())
        if prompt == selection_agent.DERIVED_COLUMN_DESIGN_PROMPT:
            return StubAgent(_derived_result())
        return StubAgent(_sample_result())

    monkeypatch.setattr(selection_agent, "build_agent", build_agent)
    events = []

    result = selection_agent.run_steps(
        "고객별 결제 성향을 분석해줘",
        {"requested_data_sentence": "고객별 결제 성향 분석"},
        ["transaction_pseudonymized"],
        SCHEMA_METADATA,
        on_step=lambda code, status, metadata: events.append(
            (code, status, metadata)
        ),
    )

    assert result["sample_metadata"]["sample_count"] == 5
    assert [(code, status) for code, status, _ in events] == [
        ("SOURCE_COLUMN_SELECTION", "RUNNING"),
        ("SOURCE_COLUMN_SELECTION", "COMPLETED"),
        ("DERIVED_COLUMN_DESIGN", "RUNNING"),
        ("DERIVED_COLUMN_DESIGN", "COMPLETED"),
        ("SYNTHETIC_SAMPLE_GENERATION", "RUNNING"),
        ("SYNTHETIC_SAMPLE_GENERATION", "COMPLETED"),
    ]
    assert events[1][2] == {"selected_table_count": 1, "source_column_count": 2}
    assert events[3][2] == {"derived_column_count": 1}
    assert events[5][2] == {"sample_count": 5}


def test_derived_comparison_symbol_is_normalized_to_executor_contract():
    selection = _selection()
    spec = selection["derived_columns"][0]["derivation_spec"]
    spec["operation"] = "compare"
    spec["parameters"] = {
        "operator": ">=",
        "left": {"column": "krw_converted_amount"},
        "right": {"literal": 10000},
    }
    selection["derived_columns"][0]["data_type"] = "boolean"

    _validate_contract(selection, SCHEMA_METADATA)

    assert spec["parameters"]["operator"] == "gte"


def test_deepseek_date_part_aliases_are_normalized_to_single_contract():
    column = {
        "name": "transaction_month",
        "data_type": "integer",
        "source_columns": ["transaction_datetime"],
        "derivation_spec": {
            "spec_version": "1.0",
            "operation": "date_part",
            "parameters": {
                "column": "transaction_datetime",
                "date_part": "month",
            },
            "evidence": "거래 일시 COMMENT",
        },
    }

    selection_agent._normalize_derivation_spec(column)
    selection_agent._validate_derivation_spec(column, {"transaction_datetime"})

    assert column["derivation_spec"]["parameters"] == {
        "source": {"column": "transaction_datetime"},
        "part": "month",
    }


def test_deepseek_flat_logical_conditions_are_normalized_recursively():
    column = {
        "name": "is_gangnam_dining",
        "data_type": "boolean",
        "source_columns": ["mcc_code", "merchant_region"],
        "derivation_spec": {
            "spec_version": "1.0",
            "operation": "logical",
            "parameters": {
                "operator": "and",
                "conditions": [
                    {
                        "column": "mcc_code",
                        "operator": "eq",
                        "value": {"literal": 5812},
                    },
                    {
                        "column": "merchant_region",
                        "operator": "eq",
                        "value": {"literal": "서울특별시 강남구"},
                    },
                ],
            },
            "evidence": "MCC 카탈로그와 지역 COMMENT",
        },
    }

    selection_agent._normalize_derivation_spec(column)
    selection_agent._validate_derivation_spec(
        column, {"mcc_code", "merchant_region"}
    )

    operands = column["derivation_spec"]["parameters"]["operands"]
    assert [operand["operation"] for operand in operands] == ["compare", "compare"]
    assert operands[0]["parameters"]["left"] == {"column": "mcc_code"}
    assert operands[1]["parameters"]["left"] == {"column": "merchant_region"}


def test_derivation_alias_conflict_is_rejected_instead_of_overwritten():
    column = {
        "source_columns": ["transaction_datetime"],
        "derivation_spec": {
            "spec_version": "1.0",
            "operation": "date_part",
            "parameters": {
                "column": "transaction_datetime",
                "source": {"column": "transaction_datetime"},
                "part": "month",
            },
            "evidence": "거래 일시 COMMENT",
        },
    }

    with pytest.raises(ValueError, match="column과 source를 함께"):
        selection_agent._normalize_derivation_spec(column)


def test_derivation_source_mismatch_reports_declared_and_referenced_columns():
    column = {
        "name": "transaction_month",
        "data_type": "integer",
        "source_columns": ["approved_at"],
        "derivation_spec": {
            "spec_version": "1.0",
            "operation": "date_part",
            "parameters": {
                "source": {"column": "transaction_datetime"},
                "part": "month",
            },
            "evidence": "거래 일시 COMMENT",
        },
    }

    with pytest.raises(ValueError) as exc_info:
        selection_agent._validate_derivation_spec(column, {"approved_at"})

    message = str(exc_info.value)
    assert "declared=['approved_at']" in message
    assert "referenced=['transaction_datetime']" in message
    assert "missing_in_expression=['approved_at']" in message
    assert "undeclared_in_expression=['transaction_datetime']" in message


def test_derived_prompt_contains_operation_specific_single_contracts():
    prompt = selection_agent.DERIVED_COLUMN_DESIGN_PROMPT

    assert '"source":{"column":"날짜·시간 컬럼명"}' in prompt
    assert '"part":"year|month|day|weekday|hour"' in prompt
    assert "conditions 키" in prompt
    assert '"operation":"compare|logical"' in prompt


def test_source_mismatch_retry_hint_contains_correct_date_and_logical_examples():
    hint = selection_agent._selection_retry_hint(
        "derived column 'transaction_month' source mismatch"
    )

    assert '"source":{"column":"컬럼명"}' in hint
    assert '"part":"month"' in hint
    assert "operation=compare" in hint


def test_selection_failure_keeps_safe_model_response_diagnostics(monkeypatch):
    class EmptyResult:
        stop_reason = "max_tokens"

        def __str__(self):
            return "응답에 JSON이 없습니다"

    monkeypatch.setattr(selection_agent, "build_agent", lambda _prompt: lambda _message: EmptyResult())
    events = []

    with pytest.raises(selection_agent.SelectionPlanningError) as raised:
        selection_agent._run_prompt_step(
            system_prompt="test",
            request_payload={},
            validate=lambda _value: None,
            step_label="파생 컬럼 정의",
            step_code="DERIVED_COLUMN_DESIGN",
            on_step=lambda code, status, metadata: events.append((code, status, metadata)),
        )

    snapshot = raised.value.failure_snapshot
    assert snapshot["failed_step"] == "DERIVED_COLUMN_DESIGN"
    assert snapshot["retry_count"] == 3
    assert snapshot["failure_code"] == "SELECTION_RULE_INVALID"
    assert snapshot["model_response"] == {
        "length": len("응답에 JSON이 없습니다"),
        "excerpt": "응답에 JSON이 없습니다",
        "finish_reason": "max_tokens",
    }
    assert events[-1][1] == "FAILED"
