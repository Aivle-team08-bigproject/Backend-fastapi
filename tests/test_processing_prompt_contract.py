from agent_runtime.data_processing.planning_agent import SYSTEM_PROMPT


def test_processing_prompt_documents_validator_parameter_contract():
    assert 'derive_date_part: {"part": "year|month|day|weekday"}' in SYSTEM_PROMPT
    assert "timezone 키는 절대 사용하지 않는다" in SYSTEM_PROMPT
    assert 'fill_missing: {"strategy": "median|mode|zero|drop_row|keep_null"}' in SYSTEM_PROMPT
    assert 'sort: {"direction": "asc|desc"}' in SYSTEM_PROMPT
    assert "필수 제약사항:" in SYSTEM_PROMPT
    assert "새 컬럼은 그것을 만드는 작업이 성공한 뒤에만" in SYSTEM_PROMPT
    assert "예를 들어 op-1에서 fraud_risk_score를 사용하고 op-9에서 처음 만드는 계획은 잘못됐다" in SYSTEM_PROMPT
    assert "추가 지시 END." in SYSTEM_PROMPT
