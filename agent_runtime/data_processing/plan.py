"""LLM이 생성하고 결정론적 executor가 소비하는 가공 계획 계약."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


OperationType = Literal[
    "cast",
    "fill_missing",
    "deduplicate",
    "derive_date_part",
    "bucketize",
    "aggregate",
    "sort",
    "select_columns",
]


class ProcessingOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: OperationType
    source_columns: list[str] = Field(default_factory=list)
    target_column: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1)


class QualityCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["not_null", "non_negative", "unique"]
    column: str


class ProcessingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: list[str] = Field(min_length=1)
    formats: list[Literal["api", "csv", "visualization", "report"]] = Field(min_length=1)


class ProcessingPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_version: Literal["1.0"] = "1.0"
    objective: str = Field(min_length=1)
    operations: list[ProcessingOperation] = Field(min_length=1)
    output: ProcessingOutput
    quality_checks: list[QualityCheck] = Field(default_factory=list)
    explanation: str = Field(min_length=1)

    @model_validator(mode="after")
    def operation_ids_are_unique(self) -> "ProcessingPlan":
        identifiers = [operation.id for operation in self.operations]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("processing operation ids must be unique")
        return self


class ProcessingPlanError(ValueError):
    """가공 계획이 실행 계약 또는 보안 정책을 위반했다."""


def validate_processing_plan(plan: ProcessingPlan, selection: dict) -> None:
    """승인된 선별 컬럼에서 시작해 모든 operation의 컬럼 계보를 검증한다."""

    available = validate_processing_operations(plan.operations, selection)

    missing_outputs = set(plan.output.columns) - available
    if missing_outputs:
        raise ProcessingPlanError(
            f"processing output references unavailable columns: {', '.join(sorted(missing_outputs))}"
        )
    for check in plan.quality_checks:
        if check.column not in available:
            raise ProcessingPlanError(f"quality check references unavailable column: {check.column}")


def validate_processing_operations(
    operations: list[ProcessingOperation], selection: dict
) -> set[str]:
    """중간 단계 operation까지 승인 컬럼 계보와 실행 계약을 누적 검증한다."""

    available = {
        str(column.get("column"))
        for column in selection.get("source_columns") or []
        if isinstance(column, dict) and column.get("column")
    }
    available.update(str(column) for column in (selection.get("selection_query") or {}).get("columns") or [])
    if not available:
        raise ProcessingPlanError("approved selection has no executable source columns")

    for operation in operations:
        missing = set(operation.source_columns) - available
        if missing:
            raise ProcessingPlanError(
                f"operation {operation.id} references unavailable columns: {', '.join(sorted(missing))}"
            )
        _validate_operation(operation)

        if operation.type == "aggregate":
            group_by = _string_list(operation.parameters.get("group_by"), "aggregate.group_by")
            if set(group_by) - available:
                raise ProcessingPlanError("aggregate group_by references unavailable columns")
            metrics = operation.parameters.get("metrics")
            if not isinstance(metrics, list) or not metrics:
                raise ProcessingPlanError("aggregate.metrics must be a non-empty list")
            targets = set(group_by)
            for metric in metrics:
                if not isinstance(metric, dict):
                    raise ProcessingPlanError("aggregate metric must be an object")
                column = str(metric.get("column") or "")
                function = str(metric.get("function") or "")
                target = str(metric.get("target") or "")
                if column not in available or function not in {"sum", "count", "count_distinct", "avg", "min", "max"} or not target:
                    raise ProcessingPlanError("aggregate metric is invalid")
                targets.add(target)
            available = targets
        elif operation.type == "select_columns":
            columns = _string_list(operation.parameters.get("columns"), "select_columns.columns")
            if set(columns) - available:
                raise ProcessingPlanError("select_columns references unavailable columns")
            available = set(columns)
        elif operation.target_column:
            available.add(operation.target_column)

    return available


def processing_plan_sha256(plan: ProcessingPlan | dict) -> str:
    value = plan.model_dump(mode="json") if isinstance(plan, ProcessingPlan) else plan
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validate_operation(operation: ProcessingOperation) -> None:
    allowed_parameters = {
        "cast": {"data_type"},
        "fill_missing": {"strategy"},
        "deduplicate": set(),
        "derive_date_part": {"part"},
        "bucketize": {"bins", "labels"},
        "aggregate": {"group_by", "metrics"},
        "sort": {"direction"},
        "select_columns": {"columns"},
    }[operation.type]
    unexpected = set(operation.parameters) - allowed_parameters
    if unexpected:
        raise ProcessingPlanError(
            f"operation {operation.id} has unsupported parameters: {', '.join(sorted(unexpected))}"
        )
    if operation.type in {"derive_date_part", "bucketize"} and (
        len(operation.source_columns) != 1 or not operation.target_column
    ):
        raise ProcessingPlanError(f"{operation.type} requires one source column and target_column")
    if operation.type == "cast":
        data_type = operation.parameters.get("data_type")
        if not operation.source_columns or data_type not in {"string", "integer", "number", "date", "datetime"}:
            raise ProcessingPlanError("cast requires source columns and an allowed data_type")
    if operation.type == "fill_missing":
        strategy = operation.parameters.get("strategy")
        if not operation.source_columns or strategy not in {"median", "mode", "zero", "drop_row", "keep_null"}:
            raise ProcessingPlanError("fill_missing strategy is invalid")
    if operation.type == "derive_date_part" and operation.parameters.get("part") not in {"year", "month", "day", "weekday"}:
        raise ProcessingPlanError("derive_date_part.part is invalid")
    if operation.type == "bucketize":
        bins = operation.parameters.get("bins")
        if not isinstance(bins, list) or len(bins) < 2 or not all(isinstance(item, (int, float)) for item in bins):
            raise ProcessingPlanError("bucketize.bins must contain at least two numbers")
    if operation.type == "sort" and operation.parameters.get("direction", "asc") not in {"asc", "desc"}:
        raise ProcessingPlanError("sort.direction is invalid")
    if operation.type == "select_columns":
        _string_list(operation.parameters.get("columns"), "select_columns.columns")


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise ProcessingPlanError(f"{field} must be a non-empty string list")
    return value
