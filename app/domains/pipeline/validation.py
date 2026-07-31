"""단계 산출물 검증 — Supervisor가 다음 단계로 넘길지 판단하는 근거.

agent_runtime/의 각 에이전트는 자기 산출물이 쓸만한지 스스로 판단하지 않는다(그쪽 모듈
주석에 명시). 그 판단을 여기서 한다.
"""

from app.domains.pipeline.model import FailureCode, StageName


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
        required = [
            "selected_tables",
            "source_columns",
            "derived_columns",
            "selection_query",
            "sample_columns",
            "sample_rows",
            "sample_metadata",
        ]
        errors.extend(_missing(required, output))
        if not output.get("selected_tables"):
            errors.append("selected_tables must not be empty")
            failure_code = FailureCode.INSUFFICIENT_DATA
        source_columns = output.get("source_columns")
        derived_columns = output.get("derived_columns")
        selection_query = output.get("selection_query")
        if not isinstance(source_columns, list) or not source_columns:
            errors.append("source_columns must be a non-empty list")
        if not isinstance(derived_columns, list):
            errors.append("derived_columns must be a list")
        if not isinstance(selection_query, dict):
            errors.append("selection_query must be a JSON object")
        elif any(
            key in selection_query
            for key in ("top_k", "limit", "vector_similarity")
        ):
            errors.append("selection_query must not control row count or vector search")
        elif isinstance(source_columns, list):
            source_names = {
                column.get("column")
                for column in source_columns
                if isinstance(column, dict) and column.get("column")
            }
            if set(selection_query.get("columns") or []) != source_names:
                errors.append("selection_query.columns must match source_columns")
        if isinstance(derived_columns, list) and isinstance(source_columns, list):
            source_names = {
                column.get("column")
                for column in source_columns
                if isinstance(column, dict) and column.get("column")
            }
            for index, column in enumerate(derived_columns):
                references = column.get("source_columns") if isinstance(column, dict) else None
                if (
                    not isinstance(references, list)
                    or not references
                    or not set(references).issubset(source_names)
                ):
                    errors.append(
                        f"derived_columns[{index}] must reference selected source columns"
                    )
        sample_columns = output.get("sample_columns")
        sample_rows = output.get("sample_rows")
        sample_metadata = output.get("sample_metadata")
        if not isinstance(sample_columns, list) or not sample_columns:
            errors.append("sample_columns must be a non-empty list")
        else:
            column_names = [
                column.get("name")
                for column in sample_columns
                if isinstance(column, dict)
            ]
            if len(column_names) != len(sample_columns) or any(not name for name in column_names):
                errors.append("every sample column must have a name")
            elif len(set(column_names)) != len(column_names):
                errors.append("sample column names must be unique")
            elif not isinstance(sample_rows, list) or len(sample_rows) != 5:
                errors.append("sample_rows must contain exactly 5 rows")
            else:
                expected_keys = set(column_names)
                for index, row in enumerate(sample_rows):
                    if not isinstance(row, dict) or set(row) != expected_keys:
                        errors.append(
                            f"sample_rows[{index}] columns must exactly match sample_columns"
                        )
        if not isinstance(sample_metadata, dict):
            errors.append("sample_metadata must be a JSON object")
        elif (
            sample_metadata.get("is_synthetic") is not True
            or sample_metadata.get("sample_count") != 5
        ):
            errors.append("sample_metadata must identify exactly 5 synthetic rows")
        if errors and failure_code is None:
            failure_code = FailureCode.SCHEMA_INVALID

    if stage_name == StageName.DATA_RETRIEVAL:
        required = [
            "source_type",
            "input_csv_path",
            "input_sha256",
            "input_row_count",
            "source_csv_path",
            "source_sha256",
            "encoding",
            "row_count",
            "columns",
            "applied_filters",
            "unmapped_filters",
            "raw_rows_stored_in_database",
        ]
        errors.extend(_missing(required, output))
        if not isinstance(output.get("row_count"), int) or output.get("row_count", 0) <= 0:
            errors.append("row_count must be a positive integer")
        if not output.get("columns"):
            errors.append("columns must not be empty")
        if output.get("raw_rows_stored_in_database") is not False:
            errors.append("raw rows must not be stored in the supervisor database")
        if output.get("unmapped_filters"):
            errors.append("unmapped_filters must be empty")
        input_count = output.get("input_row_count")
        selected_count = output.get("row_count")
        if (
            isinstance(input_count, int)
            and isinstance(selected_count, int)
            and selected_count > input_count
        ):
            errors.append("selected row_count must not exceed input_row_count")
        if errors:
            raw_code = output.get("_failure_code")
            try:
                failure_code = FailureCode(raw_code) if raw_code else FailureCode.INSUFFICIENT_DATA
            except ValueError:
                failure_code = FailureCode.INSUFFICIENT_DATA

    if stage_name == StageName.DATA_PROCESSING:
        required = [
            "processed_columns",
            "api_result",
            "csv_columns",
            "visualization",
            "report",
            "processing_explanation",
            "quality_report",
        ]
        errors.extend(_missing(required, output))
        if not output.get("processed_columns"):
            errors.append("processed_columns must not be empty")
            failure_code = FailureCode.PROCESSING_RULE_INVALID
        if errors and failure_code is None:
            raw_code = output.get("_failure_code")
            try:
                failure_code = FailureCode(raw_code) if raw_code else FailureCode.FORMAT_INVALID
            except ValueError:
                failure_code = FailureCode.PROCESSING_RULE_INVALID

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
