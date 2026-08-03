"""Deterministic transformations for selected pseudonymized data."""

from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import os
import statistics
from collections import Counter
from datetime import date, datetime
from typing import Any

from agent_runtime.data_processing.plan import (
    ProcessingOperation,
    ProcessingPlan,
    processing_plan_sha256,
    validate_processing_plan,
)


DIRECT_IDENTIFIER_NAMES = {
    "customer_id",
    "member_id",
    "user_id",
    "name",
    "email",
    "phone",
    "phone_number",
    "card_number",
}
NULL_TEXT = {"", "null", "none", "nan", "n/a", "na"}


class ProcessingError(ValueError):
    """Raised when selected data cannot be processed safely."""


def process_payload(payload: dict[str, Any]) -> dict[str, Any]:
    rows = _extract_rows(payload)
    if not rows:
        raise ProcessingError("selected_rows is required and must not be empty")

    max_rows = int(os.getenv("DATA_PROCESSING_MAX_ROWS", "50000"))
    if len(rows) > max_rows:
        raise ProcessingError(f"selected_rows exceeds DATA_PROCESSING_MAX_ROWS ({max_rows})")

    policies = payload.get("column_policies") or {}
    columns = list(dict.fromkeys(key for row in rows for key in row))
    normalized = [{column: _normalize_null(row.get(column)) for column in columns} for row in rows]
    missing_before = _missing_counts(normalized, columns)

    raw_plan = payload.get("processing_plan")
    if raw_plan:
        plan = ProcessingPlan.model_validate(raw_plan)
        validate_processing_plan(plan, payload.get("selection") or {})
        normalized, columns, operation_audit = _execute_plan(normalized, columns, plan)
        columns = list(plan.output.columns)
        normalized = [{column: row.get(column) for column in columns} for row in normalized]
        conversion_audit = [item for item in operation_audit if item["type"] == "cast"]
        missing_audit = [item for item in operation_audit if item["type"] == "fill_missing"]
        duplicates_removed = sum(
            int(item.get("affected_rows", 0))
            for item in operation_audit
            if item["type"] == "deduplicate"
        )
        imputation_count = sum(
            int(item.get("affected_rows", 0))
            for item in operation_audit
            if item["type"] == "fill_missing" and item.get("strategy") not in {"keep_null", "drop_row"}
        )
    else:
        plan = None
        normalized, columns, operation_audit, conversion_audit, missing_audit, duplicates_removed, imputation_count = (
            _execute_legacy_defaults(normalized, columns, policies)
        )

    anonymization_key = os.getenv("DATA_ANONYMIZATION_KEY") or os.getenv("ANON_HASH_SALT", "")
    anonymization_audit: list[dict[str, Any]] = []
    for column in columns:
        policy = policies.get(column, {})
        sensitivity = policy.get("sensitivity") or _inferred_sensitivity(column)
        if sensitivity == "direct_identifier":
            values = [row[column] for row in normalized if row[column] is not None]
            if values and not anonymization_key:
                raise ProcessingError("DATA_ANONYMIZATION_KEY is required for direct identifiers")
            for row in normalized:
                if row[column] is not None:
                    row[column] = _tokenize(row[column], anonymization_key)
            anonymization_audit.append(
                {
                    "column": column,
                    "method": "HMAC-SHA256 pseudonymization",
                    "affected_rows": len(values),
                }
            )
        elif sensitivity == "quasi_identifier" and column.lower() in {"age", "customer_age", "member_age"}:
            affected = 0
            for row in normalized:
                if isinstance(row[column], (int, float)):
                    lower = int(row[column]) // 10 * 10
                    row[column] = f"{lower}-{lower + 9}"
                    affected += 1
            anonymization_audit.append(
                {"column": column, "method": "10-year age band generalization", "affected_rows": affected}
            )

    _run_quality_checks(normalized, plan)

    output_formats = set(plan.output.formats) if plan else _output_formats(payload.get("analysis", {}))
    delivery_channel = str(payload.get("analysis", {}).get("delivery_channel", "api")).lower()
    csv_artifact = _build_csv(normalized, columns) if "csv" in output_formats else None
    api_result = {
        "items": normalized if delivery_channel == "api" else [],
        "meta": {"row_count": len(normalized)},
    }
    visualization = _build_visualization(normalized, columns) if "visualization" in output_formats else None
    report = _build_report(payload, len(rows), len(normalized), imputation_count, duplicates_removed) if "report" in output_formats else None

    return {
        "processing_engine": "llm-plan-deterministic-executor-v1" if plan else "deterministic-python-v1",
        "processing_plan": plan.model_dump(mode="json") if plan else None,
        "processing_plan_sha256": processing_plan_sha256(plan) if plan else None,
        "execution_audit": {
            "approved_selection_stage_run_id": (
                payload.get("approval_audit") or {}
            ).get("stage_run_id"),
            "approved_selection_sha256": (
                payload.get("approval_audit") or {}
            ).get("sha256"),
            "approved_at": (payload.get("approval_audit") or {}).get("approved_at"),
            "reviewer_id": (payload.get("approval_audit") or {}).get("reviewer_id"),
            "reviewer_name": (payload.get("approval_audit") or {}).get("reviewer_name"),
            "processing_plan_sha256": processing_plan_sha256(plan) if plan else None,
            "processing_agent": payload.get("processing_agent"),
        },
        "processed_columns": columns,
        "api_result": api_result,
        "csv_columns": columns if csv_artifact else [],
        "csv_artifact": csv_artifact,
        "visualization": visualization,
        "report": report,
        "processing_explanation": {
            "summary": plan.explanation if plan else "Selected rows were normalized, deduplicated, imputed, and pseudonymized by explicit rules.",
            "operation_execution": operation_audit,
            "missing_value_handling": missing_audit,
            "format_conversion": {"csv_encoding": "utf-8-sig", "columns": conversion_audit},
            "anonymization": anonymization_audit,
            "duplicate_handling": {"strategy": "exact-row", "removed_rows": duplicates_removed},
            "safeguards": [
                "Raw selected rows are not sent to an LLM.",
                "Direct identifiers are never included in the audit output.",
            ],
        },
        "quality_report": {
            "input_row_count": len(rows),
            "output_row_count": len(normalized),
            "duplicates_removed": duplicates_removed,
            "missing_before": missing_before,
            "missing_after": _missing_counts(normalized, columns),
            "imputation_count": imputation_count,
            "contains_raw_identifiers_in_audit": False,
        },
    }


def _extract_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    selection = payload.get("selection") or {}
    candidates = [
        payload.get("selected_rows"),
        selection.get("selected_rows"),
        selection.get("rows"),
        selection.get("records"),
    ]
    rows = next((item for item in candidates if isinstance(item, list)), None)
    if rows is None:
        return []
    if not all(isinstance(row, dict) for row in rows):
        raise ProcessingError("every selected row must be a JSON object")
    return [dict(row) for row in rows]


def _execute_legacy_defaults(
    rows: list[dict[str, Any]],
    columns: list[str],
    policies: dict[str, Any],
) -> tuple[
    list[dict[str, Any]],
    list[str],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    int,
    int,
]:
    """기존 직접 호출 계약을 보존하는 기본 실행 계획."""

    conversion_audit: list[dict[str, Any]] = []
    for column in columns:
        data_type = (policies.get(column) or {}).get("data_type")
        if not data_type:
            continue
        converted = 0
        for row in rows:
            old_value = row[column]
            new_value = _convert(old_value, data_type)
            converted += new_value != old_value
            row[column] = new_value
        conversion_audit.append(
            {"type": "cast", "column": column, "data_type": data_type, "converted_rows": converted}
        )

    before_dedup = len(rows)
    rows = _deduplicate(rows, columns)
    duplicates_removed = before_dedup - len(rows)
    operation_audit: list[dict[str, Any]] = [
        {"id": "legacy-deduplicate", "type": "deduplicate", "affected_rows": duplicates_removed}
    ]
    operation_audit.extend(conversion_audit)

    missing_audit: list[dict[str, Any]] = []
    imputation_count = 0
    for column in columns:
        policy = policies.get(column) or {}
        sensitivity = policy.get("sensitivity") or _inferred_sensitivity(column)
        missing_indexes = [index for index, row in enumerate(rows) if row[column] is None]
        if not missing_indexes or sensitivity == "direct_identifier":
            if missing_indexes:
                missing_audit.append(
                    {"type": "fill_missing", "column": column, "strategy": "keep_null", "affected_rows": len(missing_indexes)}
                )
            continue
        strategy = policy.get("missing_strategy") or _default_missing_strategy(rows, column)
        fill_value = _fill_value(rows, column, strategy)
        if fill_value is None:
            missing_audit.append(
                {"type": "fill_missing", "column": column, "strategy": "keep_null", "affected_rows": len(missing_indexes)}
            )
            continue
        for index in missing_indexes:
            rows[index][column] = fill_value
        imputation_count += len(missing_indexes)
        missing_audit.append(
            {"type": "fill_missing", "column": column, "strategy": strategy, "affected_rows": len(missing_indexes)}
        )
    operation_audit.extend(missing_audit)
    return rows, columns, operation_audit, conversion_audit, missing_audit, duplicates_removed, imputation_count


def _execute_plan(
    rows: list[dict[str, Any]], columns: list[str], plan: ProcessingPlan
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    audit: list[dict[str, Any]] = []
    for operation in plan.operations:
        before = len(rows)
        if operation.type == "cast":
            converted = 0
            data_type = str(operation.parameters["data_type"])
            for column in operation.source_columns:
                for row in rows:
                    old_value = row.get(column)
                    new_value = _convert(old_value, data_type)
                    converted += new_value != old_value
                    row[column] = new_value
            detail = {"converted_rows": converted, "data_type": data_type}
        elif operation.type == "fill_missing":
            rows, affected = _execute_fill_missing(rows, operation)
            detail = {"strategy": operation.parameters["strategy"], "affected_rows": affected}
        elif operation.type == "deduplicate":
            subset = operation.source_columns or columns
            rows = _deduplicate(rows, subset)
            detail = {"affected_rows": before - len(rows), "columns": subset}
        elif operation.type == "derive_date_part":
            source = operation.source_columns[0]
            target = str(operation.target_column)
            part = str(operation.parameters["part"])
            for row in rows:
                row[target] = _date_part(row.get(source), part)
            if target not in columns:
                columns.append(target)
            detail = {"part": part, "target_column": target, "affected_rows": len(rows)}
        elif operation.type == "bucketize":
            source = operation.source_columns[0]
            target = str(operation.target_column)
            bins = sorted(float(item) for item in operation.parameters["bins"])
            labels = operation.parameters.get("labels")
            if labels is not None and (not isinstance(labels, list) or len(labels) != len(bins) - 1):
                raise ProcessingError("bucketize labels must match bin intervals")
            for row in rows:
                row[target] = _bucket(row.get(source), bins, labels)
            if target not in columns:
                columns.append(target)
            detail = {"bins": bins, "target_column": target, "affected_rows": len(rows)}
        elif operation.type == "aggregate":
            rows, columns = _aggregate(rows, operation)
            detail = {"affected_rows": before - len(rows), "output_rows": len(rows)}
        elif operation.type == "sort":
            sort_columns = operation.source_columns
            reverse = operation.parameters.get("direction", "asc") == "desc"
            rows.sort(key=lambda row: tuple(_sortable(row.get(column)) for column in sort_columns), reverse=reverse)
            detail = {"columns": sort_columns, "direction": "desc" if reverse else "asc"}
        elif operation.type == "select_columns":
            columns = list(operation.parameters["columns"])
            rows = [{column: row.get(column) for column in columns} for row in rows]
            detail = {"columns": columns}
        else:  # pragma: no cover - Pydantic Literal가 먼저 차단한다.
            raise ProcessingError(f"unsupported processing operation: {operation.type}")
        audit.append(
            {
                "id": operation.id,
                "type": operation.type,
                "source_columns": operation.source_columns,
                "reason": operation.reason,
                **detail,
            }
        )
    return rows, columns, audit


def _execute_fill_missing(
    rows: list[dict[str, Any]], operation: ProcessingOperation
) -> tuple[list[dict[str, Any]], int]:
    strategy = str(operation.parameters["strategy"])
    affected = sum(
        row.get(column) is None for row in rows for column in operation.source_columns
    )
    if strategy == "drop_row":
        return [
            row for row in rows if all(row.get(column) is not None for column in operation.source_columns)
        ], affected
    if strategy == "keep_null":
        return rows, affected
    for column in operation.source_columns:
        fill_value = _fill_value(rows, column, strategy)
        if fill_value is None:
            continue
        for row in rows:
            if row.get(column) is None:
                row[column] = fill_value
    return rows, affected


def _date_part(value: Any, part: str) -> Any:
    if value is None:
        return None
    try:
        parsed = value if isinstance(value, (date, datetime)) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if part == "year":
        return parsed.year
    if part == "month":
        return f"{parsed.year:04d}-{parsed.month:02d}"
    if part == "day":
        return parsed.date().isoformat() if isinstance(parsed, datetime) else parsed.isoformat()
    return parsed.weekday()


def _bucket(value: Any, bins: list[float], labels: Any) -> str | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    for index, (lower, upper) in enumerate(zip(bins, bins[1:])):
        if lower <= numeric < upper or (index == len(bins) - 2 and numeric == upper):
            return str(labels[index]) if labels else f"{lower:g}-{upper:g}"
    return None


def _aggregate(
    rows: list[dict[str, Any]], operation: ProcessingOperation
) -> tuple[list[dict[str, Any]], list[str]]:
    group_by = list(operation.parameters["group_by"])
    metrics = list(operation.parameters["metrics"])
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(tuple(row.get(column) for column in group_by), []).append(row)
    result = []
    for key, members in grouped.items():
        output = dict(zip(group_by, key))
        for metric in metrics:
            values = [row.get(metric["column"]) for row in members if row.get(metric["column"]) is not None]
            function = metric["function"]
            if function == "count":
                value = len(values)
            elif function == "count_distinct":
                value = len({repr(item) for item in values})
            else:
                numeric = [float(item) for item in values if isinstance(item, (int, float))]
                if not numeric:
                    value = None
                elif function == "sum":
                    value = sum(numeric)
                elif function == "avg":
                    value = sum(numeric) / len(numeric)
                elif function == "min":
                    value = min(numeric)
                else:
                    value = max(numeric)
            output[metric["target"]] = value
        result.append(output)
    return result, group_by + [str(metric["target"]) for metric in metrics]


def _sortable(value: Any) -> tuple[bool, str]:
    return value is None, str(value)


def _run_quality_checks(rows: list[dict[str, Any]], plan: ProcessingPlan | None) -> None:
    if plan is None:
        return
    for check in plan.quality_checks:
        values = [row.get(check.column) for row in rows]
        if check.type == "not_null" and any(value is None for value in values):
            raise ProcessingError(f"quality check failed: {check.column} contains null")
        if check.type == "non_negative" and any(
            isinstance(value, (int, float)) and value < 0 for value in values
        ):
            raise ProcessingError(f"quality check failed: {check.column} contains negative values")
        if check.type == "unique" and len(values) != len({repr(value) for value in values}):
            raise ProcessingError(f"quality check failed: {check.column} is not unique")


def _normalize_null(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return None if value.lower() in NULL_TEXT else value
    return value


def _convert(value: Any, data_type: str | None) -> Any:
    if value is None or not data_type:
        return value
    try:
        if data_type == "integer":
            return int(float(str(value).replace(",", "")))
        if data_type == "number":
            return float(str(value).replace(",", ""))
        if data_type in {"date", "datetime"}:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.date().isoformat() if data_type == "date" else parsed.isoformat()
        if data_type == "string":
            return str(value)
    except (TypeError, ValueError):
        return None
    return value


def _deduplicate(rows: list[dict[str, Any]], columns: list[str]) -> list[dict[str, Any]]:
    seen: set[tuple[str, ...]] = set()
    result = []
    for row in rows:
        marker = tuple(repr(row[column]) for column in columns)
        if marker not in seen:
            seen.add(marker)
            result.append(row)
    return result


def _default_missing_strategy(rows: list[dict[str, Any]], column: str) -> str:
    values = [row[column] for row in rows if row[column] is not None]
    return "median" if values and all(isinstance(value, (int, float)) for value in values) else "mode"


def _fill_value(rows: list[dict[str, Any]], column: str, strategy: str) -> Any:
    values = [row[column] for row in rows if row[column] is not None]
    if not values:
        return None
    if strategy == "median":
        numeric = [float(value) for value in values if isinstance(value, (int, float))]
        return statistics.median(numeric) if numeric else None
    if strategy == "mode":
        return Counter(values).most_common(1)[0][0]
    if strategy == "zero":
        return 0
    return None


def _inferred_sensitivity(column: str) -> str:
    return "direct_identifier" if column.lower() in DIRECT_IDENTIFIER_NAMES else "none"


def _tokenize(value: Any, key: str) -> str:
    digest = hmac.new(key.encode("utf-8"), str(value).encode("utf-8"), hashlib.sha256).hexdigest()
    return f"anon_{digest[:24]}"


def _missing_counts(rows: list[dict[str, Any]], columns: list[str]) -> dict[str, int]:
    return {column: sum(row[column] is None for row in rows) for column in columns}


def _output_formats(analysis: dict[str, Any]) -> set[str]:
    raw = analysis.get("output_formats") or analysis.get("output_format") or ["csv"]
    if isinstance(raw, str):
        raw = [raw]
    return {str(item).lower() for item in raw}


def _build_csv(rows: list[dict[str, Any]], columns: list[str]) -> dict[str, Any]:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    content = stream.getvalue().encode("utf-8-sig")
    return {
        "encoding": "utf-8-sig",
        "content_base64": base64.b64encode(content).decode("ascii"),
        "sha256": hashlib.sha256(content).hexdigest(),
        "byte_size": len(content),
    }


def _build_visualization(rows: list[dict[str, Any]], columns: list[str]) -> dict[str, Any] | None:
    if not columns:
        return None
    dimension = next((column for column in columns if not all(isinstance(row[column], (int, float)) for row in rows)), columns[0])
    counts = Counter(str(row[dimension]) for row in rows)
    return {
        "chart_type": "bar",
        "x": dimension,
        "y": "row_count",
        "series": [{"label": label, "value": value} for label, value in counts.most_common(20)],
    }


def _build_report(payload: dict[str, Any], input_count: int, output_count: int, imputed: int, duplicates: int) -> dict[str, Any]:
    requirement = payload.get("raw_requirement", "")
    return {
        "title": "요구사항 기반 데이터 가공 보고서",
        "summary": (
            f"요구사항 '{requirement}'에 따라 {input_count}건을 입력받아 {output_count}건을 산출했습니다. "
            f"결측값 {imputed}건을 보정하고 중복 {duplicates}건을 제거했습니다."
        ),
    }
