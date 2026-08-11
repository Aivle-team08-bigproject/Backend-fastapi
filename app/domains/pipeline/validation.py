"""단계 산출물 검증 — Supervisor가 다음 단계로 넘길지 판단하는 근거.

agent_runtime/의 각 에이전트는 자기 산출물이 쓸만한지 스스로 판단하지 않는다(그쪽 모듈
주석에 명시). 그 판단을 여기서 한다.
"""

import base64
import binascii
import csv
import hashlib
import io

from app.domains.pipeline.model import FailureCode, StageName


def validate_stage_output(stage_name: StageName, output: dict) -> dict:
    runtime_failure = _runtime_failure(stage_name, output)
    if runtime_failure is not None:
        return runtime_failure

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
            "interpretations",
            "catalog_issues",
            "catalog_matches",
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
            filters = selection_query.get("filters")
            if not isinstance(filters, dict):
                errors.append("selection_query.filters must be a JSON object")
            else:
                for column_name, condition in filters.items():
                    if column_name not in source_names:
                        errors.append(
                            f"selection_query.filters.{column_name} must reference a source column"
                        )
                    if not isinstance(condition, dict):
                        errors.append(
                            f"selection_query.filters.{column_name} must be a JSON object"
                        )
                        continue
                    if condition.get("operator") not in {
                        "eq",
                        "in",
                        "gte",
                        "lte",
                        "between",
                        "starts_with",
                    }:
                        errors.append(
                            f"selection_query.filters.{column_name} has an unsupported operator"
                        )
                    if not condition.get("reason") or not condition.get("evidence"):
                        errors.append(
                            f"selection_query.filters.{column_name} must include reason and evidence"
                        )
        if not isinstance(output.get("interpretations"), list):
            errors.append("interpretations must be a list")
        if not isinstance(output.get("catalog_issues"), list):
            errors.append("catalog_issues must be a list")
        if not isinstance(output.get("catalog_matches"), list):
            errors.append("catalog_matches must be a list")
        if isinstance(derived_columns, list) and isinstance(source_columns, list):
            available_names = {
                column.get("column")
                for column in source_columns
                if isinstance(column, dict) and column.get("column")
            }
            for index, column in enumerate(derived_columns):
                references = column.get("source_columns") if isinstance(column, dict) else None
                if (
                    not isinstance(references, list)
                    or not references
                    or not set(references).issubset(available_names)
                ):
                    errors.append(
                        f"derived_columns[{index}] must reference source or earlier derived columns"
                    )
                spec = column.get("derivation_spec") if isinstance(column, dict) else None
                if not isinstance(spec, dict) or spec.get("spec_version") != "1.0":
                    errors.append(f"derived_columns[{index}] must include derivation_spec version 1.0")
                name = column.get("name") if isinstance(column, dict) else None
                if name in available_names:
                    errors.append(f"derived_columns[{index}].name must be unique")
                elif name:
                    available_names.add(name)
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

    if stage_name == StageName.DATA_PROCESSING:
        required = [
            "processing_plan",
            "processing_plan_sha256",
            "execution_audit",
            "processed_columns",
            "api_result",
            "csv_columns",
            "csv_artifact",
            "visualization",
            "report",
            "processing_explanation",
            "quality_report",
        ]
        errors.extend(_missing(required, output))
        if not output.get("processed_columns"):
            errors.append("processed_columns must not be empty")
            failure_code = FailureCode.PROCESSING_RULE_INVALID
        if not isinstance(output.get("processing_plan"), dict):
            errors.append("processing_plan must be a JSON object")
            failure_code = FailureCode.PROCESSING_RULE_INVALID
        if not isinstance(output.get("processing_plan_sha256"), str):
            errors.append("processing_plan_sha256 must be a string")
            failure_code = FailureCode.PROCESSING_RULE_INVALID
        elif isinstance(output.get("processing_plan"), dict):
            try:
                from agent_runtime.data_processing.plan import (
                    ProcessingPlan,
                    processing_plan_sha256,
                )

                parsed_plan = ProcessingPlan.model_validate(output["processing_plan"])
                if processing_plan_sha256(parsed_plan) != output["processing_plan_sha256"]:
                    errors.append("processing_plan_sha256 does not match processing_plan")
            except ValueError as exc:
                errors.append(f"processing_plan is invalid: {exc}")
            if errors:
                failure_code = FailureCode.PROCESSING_RULE_INVALID
        csv_errors = _validate_csv_artifact(output)
        if csv_errors:
            errors.extend(csv_errors)
            if failure_code is None:
                failure_code = FailureCode.FORMAT_INVALID
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


def _validate_csv_artifact(output: dict) -> list[str]:
    errors: list[str] = []
    columns = output.get("csv_columns")
    processed_columns = output.get("processed_columns")
    plan_output_columns = (
        ((output.get("processing_plan") or {}).get("output") or {}).get("columns")
        if isinstance(output.get("processing_plan"), dict)
        else None
    )
    if not isinstance(columns, list) or not columns or not all(
        isinstance(column, str) and column for column in columns
    ):
        errors.append("csv_columns must be a non-empty string list")
    elif columns != processed_columns:
        errors.append("csv_columns must match processed_columns")
    elif columns != plan_output_columns:
        errors.append("csv_columns must match processing_plan.output.columns")

    artifact = output.get("csv_artifact")
    if not isinstance(artifact, dict):
        return [*errors, "csv_artifact must be a JSON object"]
    if artifact.get("encoding") != "utf-8-sig":
        errors.append("csv_artifact.encoding must be utf-8-sig")

    encoded = artifact.get("content_base64")
    # AgentCore Runtime은 이 검증을 마친 뒤 S3에 직접 저장하고 base64 본문을 제거한다.
    # Supervisor는 내려온 key/무결성 메타데이터만 DB와 Spring 전달 경계에 기록한다.
    if artifact.get("storage_backend") == "s3" and encoded is None:
        required = ("storage_key", "mime_type", "filename", "sha256", "byte_size")
        missing = [field for field in required if not artifact.get(field)]
        if missing:
            return [*errors, f"runtime S3 csv_artifact missing fields: {', '.join(missing)}"]
        if not isinstance(artifact.get("byte_size"), int) or artifact["byte_size"] <= 0:
            errors.append("runtime S3 csv_artifact.byte_size must be a positive integer")
        if not isinstance(artifact.get("sha256"), str) or len(artifact["sha256"]) != 64:
            errors.append("runtime S3 csv_artifact.sha256 must be a SHA-256 hex string")
        return errors
    if not isinstance(encoded, str) or not encoded:
        return [*errors, "csv_artifact.content_base64 must be a non-empty string"]
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError, TypeError):
        return [*errors, "csv_artifact.content_base64 must be valid base64"]
    if not content:
        errors.append("csv_artifact content must not be empty")
    if artifact.get("byte_size") != len(content):
        errors.append("csv_artifact.byte_size does not match decoded content")
    if artifact.get("sha256") != hashlib.sha256(content).hexdigest():
        errors.append("csv_artifact.sha256 does not match decoded content")

    if isinstance(columns, list) and columns:
        try:
            header = next(
                csv.reader(io.StringIO(content.decode("utf-8-sig")), strict=True), []
            )
        except (UnicodeDecodeError, csv.Error):
            errors.append("csv_artifact content must be valid UTF-8 CSV")
        else:
            if header != columns:
                errors.append("csv_artifact header must match csv_columns")
    return errors


def _missing(keys: list[str], data: dict) -> list[str]:
    return [f"missing required key: {key}" for key in keys if key not in data]


def _runtime_failure(stage_name: StageName, output: dict) -> dict | None:
    """Agent/Worker 실패를 정상 산출물 스키마 오류로 덮어쓰지 않는다."""

    error = output.get("_agent_error") or output.get("_worker_error")
    if not error:
        return None

    default_codes = {
        StageName.REQUIREMENT_ANALYSIS: FailureCode.REQUIRED_KEY_MISSING,
        StageName.DATA_SELECTION: FailureCode.SELECTION_RULE_INVALID,
        StageName.DATA_PROCESSING: FailureCode.PROCESSING_RULE_INVALID,
        StageName.HITL_REVIEW: FailureCode.SCHEMA_INVALID,
    }
    raw_code = output.get("_failure_code")
    try:
        failure_code = FailureCode(raw_code) if raw_code else default_codes[stage_name]
    except ValueError:
        failure_code = default_codes[stage_name]

    return {
        "passed": False,
        "errors": [str(error)],
        "failure_code": failure_code.value,
    }
