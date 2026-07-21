from app.domain.enums import FailureCode, StageName


def validate_stage_output(stage_name: StageName, output: dict) -> dict:
    errors: list[str] = []
    failure_code: FailureCode | None = None

    if stage_name == StageName.REQUIREMENT_ANALYSIS:
        required = ["usage_purpose", "requested_data_sentence", "categories", "delivery_channel", "output_formats"]
        errors.extend(_missing(required, output))
        if not isinstance(output.get("categories"), dict):
            errors.append("categories must be a JSON object")
        if errors:
            failure_code = FailureCode.REQUIRED_KEY_MISSING

    if stage_name == StageName.DATA_SELECTION:
        required = ["selected_tables", "selection_query"]
        errors.extend(_missing(required, output))
        if not output.get("selected_tables"):
            errors.append("selected_tables must not be empty")
            failure_code = FailureCode.INSUFFICIENT_DATA
        if errors and failure_code is None:
            failure_code = FailureCode.SCHEMA_INVALID

    if stage_name == StageName.DATA_PROCESSING:
        required = ["processed_columns", "api_result", "csv_columns", "visualization", "report"]
        errors.extend(_missing(required, output))
        if not output.get("processed_columns"):
            errors.append("processed_columns must not be empty")
            failure_code = FailureCode.PROCESSING_RULE_INVALID
        if errors and failure_code is None:
            failure_code = FailureCode.FORMAT_INVALID

    if stage_name == StageName.HITL_REVIEW:
        required = ["approved", "reviewer", "natural_feedback"]
        errors.extend(_missing(required, output))
        if output.get("approved") is False:
            raw_code = output.get("failure_code") or FailureCode.HUMAN_REJECTED.value
            try:
                failure_code = FailureCode(raw_code)
            except ValueError:
                failure_code = FailureCode.HUMAN_REJECTED
        elif errors:
            failure_code = FailureCode.SCHEMA_INVALID

    passed = not errors
    if stage_name == StageName.HITL_REVIEW and output.get("approved") is False:
        passed = False

    return {"passed": passed, "errors": errors, "failure_code": failure_code.value if failure_code else None}


def _missing(keys: list[str], data: dict) -> list[str]:
    return [f"missing required key: {key}" for key in keys if key not in data]
