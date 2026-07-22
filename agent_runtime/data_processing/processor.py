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

    conversion_audit: list[dict[str, Any]] = []
    for column in columns:
        policy = policies.get(column, {})
        data_type = policy.get("data_type")
        converted = 0
        for row in normalized:
            old_value = row[column]
            new_value = _convert(old_value, data_type)
            if new_value != old_value:
                converted += 1
            row[column] = new_value
        if data_type:
            conversion_audit.append({"column": column, "data_type": data_type, "converted_rows": converted})

    before_dedup = len(normalized)
    normalized = _deduplicate(normalized, columns)
    duplicates_removed = before_dedup - len(normalized)

    missing_audit: list[dict[str, Any]] = []
    imputation_count = 0
    for column in columns:
        policy = policies.get(column, {})
        sensitivity = policy.get("sensitivity") or _inferred_sensitivity(column)
        missing_indexes = [index for index, row in enumerate(normalized) if row[column] is None]
        if not missing_indexes or sensitivity == "direct_identifier":
            if missing_indexes:
                missing_audit.append(
                    {"column": column, "strategy": "kept_null", "affected_rows": len(missing_indexes)}
                )
            continue

        strategy = policy.get("missing_strategy") or _default_missing_strategy(normalized, column)
        fill_value = _fill_value(normalized, column, strategy)
        if fill_value is None:
            missing_audit.append({"column": column, "strategy": "kept_null", "affected_rows": len(missing_indexes)})
            continue
        for index in missing_indexes:
            normalized[index][column] = fill_value
        imputation_count += len(missing_indexes)
        missing_audit.append(
            {"column": column, "strategy": strategy, "affected_rows": len(missing_indexes)}
        )

    anonymization_key = os.getenv("DATA_ANONYMIZATION_KEY", "")
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

    output_formats = _output_formats(payload.get("analysis", {}))
    delivery_channel = str(payload.get("analysis", {}).get("delivery_channel", "api")).lower()
    csv_artifact = _build_csv(normalized, columns) if "csv" in output_formats else None
    api_result = {
        "items": normalized if delivery_channel == "api" else [],
        "meta": {"row_count": len(normalized)},
    }
    visualization = _build_visualization(normalized, columns) if "visualization" in output_formats else None
    report = _build_report(payload, len(rows), len(normalized), imputation_count, duplicates_removed) if "report" in output_formats else None

    return {
        "processing_engine": "deterministic-python-v1",
        "processed_columns": columns,
        "api_result": api_result,
        "csv_columns": columns if csv_artifact else [],
        "csv_artifact": csv_artifact,
        "visualization": visualization,
        "report": report,
        "processing_explanation": {
            "summary": "Selected rows were normalized, deduplicated, imputed, and pseudonymized by explicit rules.",
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
