"""내부 실패 기록을 프론트 공개 계약으로 안전하게 축약한다."""

from typing import Any


STEP_MESSAGES = {
    "SOURCE_COLUMN_SELECTION": "원본 컬럼 선별 결과를 확정하지 못했습니다.",
    "DERIVED_COLUMN_DESIGN": "파생 컬럼 정의를 확정하지 못했습니다.",
    "SYNTHETIC_SAMPLE_GENERATION": "합성 샘플 생성을 완료하지 못했습니다.",
    "DEDUPLICATION_PLAN": "중복 제거 계획을 확정하지 못했습니다.",
    "MISSING_VALUE_PLAN": "결측 처리 계획을 확정하지 못했습니다.",
    "DERIVED_COLUMN_ORDER": "파생 컬럼 생성 계획을 확정하지 못했습니다.",
    "FINAL_COLUMN_VALIDATION": "최종 컬럼과 품질 검증 계획을 확정하지 못했습니다.",
}


def public_step_metadata(metadata: dict | None) -> dict | None:
    if not isinstance(metadata, dict):
        return metadata
    return {
        key: value
        for key, value in metadata.items()
        if key not in {"model_response", "candidate_processing_plan", "attempt_diagnostics"}
    }


def public_failure(
    *,
    stage: str | None,
    result: dict | None,
    validation_result: dict | None,
    rollback_to_stage: str | None,
    error_message: str | None,
) -> dict[str, Any] | None:
    if not error_message and (not validation_result or validation_result.get("passed") is not False):
        return None

    result = result or {}
    validation_result = validation_result or {}
    snapshot = result.get("failure_snapshot")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    step = snapshot.get("failed_step")
    errors = snapshot.get("validation_errors")
    if not isinstance(errors, list):
        errors = validation_result.get("errors") or []
    safe_errors = [str(error) for error in errors if error]
    failure_code = (
        snapshot.get("failure_code")
        or validation_result.get("failure_code")
        or result.get("_failure_code")
    )
    return {
        "stage": stage,
        "step": step,
        "failure_code": failure_code,
        "message": STEP_MESSAGES.get(step, "파이프라인 단계를 완료하지 못했습니다."),
        "details": {
            "retry_count": snapshot.get("retry_count"),
            "validation_errors": safe_errors,
        },
        "rollback_to_stage": rollback_to_stage,
    }
