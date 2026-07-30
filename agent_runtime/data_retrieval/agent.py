"""Validate a preselected CSV and return a safe hand-off artifact.

Raw rows stay in the CSV and are never sent to an LLM or stored in the
supervisor database.  The downstream processing worker reopens the validated
path and verifies its SHA-256 before reading rows.
"""

from __future__ import annotations

import csv
import hashlib
import os
import re
from pathlib import Path
from typing import Any

from strands import tool


class RetrievalError(ValueError):
    """Raised when a CSV source cannot be handed to data processing safely."""


FILTER_COLUMNS: dict[str, tuple[str, ...]] = {
    "성별": ("gender",),
    "연령대": ("age_band",),
    "나이": ("age_band",),
    "지역": ("resident_region", "destination_country_name"),
    "거주지역": ("resident_region",),
    "국가": ("destination_country_name",),
    "여행국가": ("destination_country_name",),
    "목적지": ("destination_country_name",),
    "업종": ("spend_category", "mcc_name"),
    "소비카테고리": ("spend_category", "mcc_name"),
    "승인채널": ("approval_channel",),
    "결제채널": ("approval_channel",),
    "인증방식": ("auth_method",),
    "소득구간": ("annual_income_band",),
}

VALUE_ALIASES: dict[str, tuple[str, ...]] = {
    "여성": ("F",),
    "여자": ("F",),
    "남성": ("M",),
    "남자": ("M",),
    "숙박": ("lodging", "호텔/숙박"),
    "호텔": ("lodging", "호텔/숙박"),
    "식음료": ("dining", "음식점"),
    "음식점": ("dining", "음식점"),
    "외식": ("dining", "음식점"),
    "쇼핑": ("shopping", "백화점"),
    "교통": ("transport", "해외 교통/승차공유"),
    "온라인": ("온라인_PG",),
    "오프라인": ("오프라인",),
}

TRAVEL_SCOPE_VALUES = {"해외여행", "해외여행결제", "여행", "여행결제"}


def _allowed_root() -> Path:
    return Path(os.getenv("DATA_RETRIEVAL_ALLOWED_ROOT", "/data")).resolve()


def _resolve_source(raw_path: str) -> Path:
    if not raw_path:
        raise RetrievalError("source_csv_path is required")
    source = Path(raw_path).expanduser().resolve()
    allowed_root = _allowed_root()
    if not source.is_relative_to(allowed_root):
        raise RetrievalError(f"source_csv_path must be inside {allowed_root}")
    if not source.is_file():
        raise RetrievalError("source CSV does not exist")
    if source.suffix.lower() != ".csv":
        raise RetrievalError("source file must have a .csv extension")
    return source


def retrieve(payload: dict) -> dict:
    source = _resolve_source(str(payload.get("source_csv_path", "")))
    max_rows = int(os.getenv("DATA_RETRIEVAL_MAX_ROWS", "50000"))
    input_sha256 = _sha256(source)

    columns: list[str] = []
    rows: list[dict[str, str]] = []
    encoding = ""
    last_error: Exception | None = None
    for candidate in ("utf-8-sig", "utf-8", "cp949"):
        try:
            with source.open("r", encoding=candidate, newline="") as csv_file:
                reader = csv.DictReader(csv_file)
                columns = list(reader.fieldnames or [])
                for row in reader:
                    rows.append(dict(row))
                    if len(rows) > max_rows:
                        raise RetrievalError(f"CSV exceeds DATA_RETRIEVAL_MAX_ROWS ({max_rows})")
            encoding = candidate
            break
        except UnicodeDecodeError as exc:
            last_error = exc
            rows = []
    else:
        raise RetrievalError(f"CSV encoding could not be detected: {last_error}")

    if not columns:
        raise RetrievalError("CSV header is required")
    if not rows:
        raise RetrievalError("CSV must contain at least one data row")

    selection = payload.get("selection") or {}
    filters = (selection.get("selection_query") or {}).get("filters") or {}
    selected_rows, applied_filters = _apply_filters(rows, columns, filters)
    if not selected_rows:
        raise RetrievalError("no CSV rows matched all selection filters")

    output_path = _output_path(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(selected_rows)

    return {
        "source_type": "preselected_csv",
        "input_csv_path": str(source),
        "input_sha256": input_sha256,
        "input_row_count": len(rows),
        "source_csv_path": str(output_path),
        "source_sha256": _sha256(output_path),
        "encoding": "utf-8-sig",
        "row_count": len(selected_rows),
        "columns": columns,
        "applied_filters": applied_filters,
        "unmapped_filters": [],
        "selection_snapshot": {
            "selected_tables": selection.get("selected_tables", []),
            "selection_query": selection.get("selection_query", {}),
        },
        "raw_rows_stored_in_database": False,
    }


def _output_path(payload: dict) -> Path:
    allowed_root = _allowed_root()
    output_root = Path(os.getenv("DATA_RETRIEVAL_OUTPUT_ROOT", str(allowed_root / "jobs"))).resolve()
    if not output_root.is_relative_to(allowed_root):
        raise RetrievalError(f"DATA_RETRIEVAL_OUTPUT_ROOT must be inside {allowed_root}")
    raw_job_id = str(payload.get("job_id") or "standalone")
    safe_job_id = re.sub(r"[^A-Za-z0-9_-]", "_", raw_job_id)
    return output_root / f"job-{safe_job_id}" / "selected.csv"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as binary_file:
        for chunk in iter(lambda: binary_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _apply_filters(
    rows: list[dict[str, str]],
    columns: list[str],
    filters: dict[str, Any],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    predicates = []
    applied: list[dict[str, Any]] = []
    for raw_key, raw_value in filters.items():
        key = str(raw_key).strip()
        candidate_columns = FILTER_COLUMNS.get(key)
        if candidate_columns is None and key in columns:
            candidate_columns = (key,)
        if candidate_columns is None:
            raise RetrievalError(f"unmapped selection filter: {key}")

        available_columns = tuple(column for column in candidate_columns if column in columns)
        if not available_columns:
            raise RetrievalError(
                f"selection filter '{key}' requires missing CSV columns: {', '.join(candidate_columns)}"
            )

        values = _split_filter_values(raw_value)
        if not values:
            raise RetrievalError(f"selection filter '{key}' has no value")

        normalized_values = {_normalize(value) for value in values}
        if key in {"업종", "소비카테고리"} and normalized_values & TRAVEL_SCOPE_VALUES:
            if "destination_country_name" not in columns:
                raise RetrievalError("travel filter requires destination_country_name")
            predicates.append(lambda row: bool(str(row.get("destination_country_name", "")).strip()))
            applied.append(
                {
                    "filter": key,
                    "requested": raw_value,
                    "columns": ["destination_country_name"],
                    "resolved_values": ["NOT_EMPTY"],
                }
            )
            continue

        resolved_values = []
        for value in values:
            resolved_values.extend(VALUE_ALIASES.get(value.strip(), (value.strip(),)))
        predicate = _build_predicate(key, available_columns, resolved_values)
        predicates.append(predicate)
        applied.append(
            {
                "filter": key,
                "requested": raw_value,
                "columns": list(available_columns),
                "resolved_values": list(dict.fromkeys(resolved_values)),
            }
        )

    selected = [row for row in rows if all(predicate(row) for predicate in predicates)]
    return selected, applied


def _build_predicate(key: str, columns: tuple[str, ...], values: list[str]):
    normalized_values = {_normalize(value) for value in values}
    age_values = _expand_age_ranges(values) if key in {"연령대", "나이"} else set()

    def predicate(row: dict[str, str]) -> bool:
        for column in columns:
            actual = _normalize(row.get(column, ""))
            if age_values and actual in age_values:
                return True
            if actual in normalized_values:
                return True
            if key in {"지역", "거주지역", "업종", "소비카테고리"} and any(
                expected and expected in actual for expected in normalized_values
            ):
                return True
        return False

    return predicate


def _split_filter_values(raw_value: Any) -> list[str]:
    if isinstance(raw_value, list):
        return [str(value).strip() for value in raw_value if str(value).strip()]
    return [value.strip() for value in re.split(r"[,，]", str(raw_value)) if value.strip()]


def _expand_age_ranges(values: list[str]) -> set[str]:
    expanded: set[str] = set()
    for value in values:
        match = re.fullmatch(r"(\d+)대\s*[~～-]\s*(\d+)대", value.strip())
        if not match:
            expanded.add(_normalize(value))
            continue
        start, end = sorted((int(match.group(1)), int(match.group(2))))
        expanded.update(_normalize(f"{age}대") for age in range(start, end + 1, 10))
    return expanded


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", "", str(value)).casefold()


@tool
def run(payload: dict) -> dict:
    """Validate one preselected CSV and return its processing hand-off metadata."""
    try:
        return {"ok": True, "data": retrieve(payload), "error_message": None}
    except RetrievalError as exc:
        return {
            "ok": False,
            "data": None,
            "error_message": str(exc),
            "failure_code": "INSUFFICIENT_DATA",
        }
