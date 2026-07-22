from app.domain.enums import FailureCode, StageName


ROLLBACK_POLICY: dict[FailureCode, StageName] = {
    FailureCode.SCHEMA_INVALID: StageName.REQUIREMENT_ANALYSIS,
    FailureCode.REQUIRED_KEY_MISSING: StageName.REQUIREMENT_ANALYSIS,
    FailureCode.FORMAT_INVALID: StageName.DATA_PROCESSING,
    FailureCode.LOGICAL_CONTRADICTION: StageName.REQUIREMENT_ANALYSIS,
    FailureCode.MISINTERPRETED_REQUIREMENT: StageName.REQUIREMENT_ANALYSIS,
    FailureCode.INSUFFICIENT_DATA: StageName.DATA_SELECTION,
    FailureCode.LOW_SIMILARITY_MATCH: StageName.DATA_SELECTION,
    FailureCode.DUPLICATED_DATA: StageName.DATA_SELECTION,
    FailureCode.OUTLIER_DETECTED: StageName.DATA_SELECTION,
    FailureCode.PROCESSING_RULE_INVALID: StageName.DATA_PROCESSING,
    FailureCode.HUMAN_REJECTED: StageName.REQUIREMENT_ANALYSIS,
}


def rollback_stage_for(failure_code: str | None) -> StageName:
    if not failure_code:
        return StageName.REQUIREMENT_ANALYSIS
    try:
        return ROLLBACK_POLICY[FailureCode(failure_code)]
    except ValueError:
        return StageName.REQUIREMENT_ANALYSIS


def classify_hitl_feedback(feedback: str) -> FailureCode:
    normalized = feedback.lower()
    if any(keyword in normalized for keyword in ["데이터 부족", "테이블", "선별", "유사도", "data", "table", "selection"]):
        return FailureCode.INSUFFICIENT_DATA
    if any(keyword in normalized for keyword in ["가공", "컬럼", "보고서", "시각화", "csv", "api", "chart", "report"]):
        return FailureCode.PROCESSING_RULE_INVALID
    if any(keyword in normalized for keyword in ["중복", "이상치", "duplicate", "outlier"]):
        return FailureCode.OUTLIER_DETECTED
    if any(keyword in normalized for keyword in ["요구사항", "해석", "의도", "requirement", "intent"]):
        return FailureCode.MISINTERPRETED_REQUIREMENT
    return FailureCode.HUMAN_REJECTED
