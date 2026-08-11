from agent_runtime.data_processing.planning_agent import (
    DERIVED_COLUMN_ORDER_PROMPT,
    FINAL_COLUMN_VALIDATION_PROMPT,
    MISSING_VALUE_PLAN_PROMPT,
)


def test_processing_prompt_documents_validator_parameter_contract():
    assert 'fill_missing strategy는 median, mode, zero, drop_row, keep_null 중 하나다.' in MISSING_VALUE_PLAN_PROMPT
    assert 'derive_date_part parameters: part, timezone만 사용한다.' in DERIVED_COLUMN_ORDER_PROMPT
    assert 'sort parameters: direction만 사용한다.' in DERIVED_COLUMN_ORDER_PROMPT
    assert '원본 컬럼과 앞 operation에서 만든 target_column만 참조한다.' in DERIVED_COLUMN_ORDER_PROMPT
    assert '승인된 선별 계획 및 앞 operation에서 사용 가능한 컬럼만 최종 출력에 넣는다.' in FINAL_COLUMN_VALIDATION_PROMPT
