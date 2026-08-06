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
    "compare",
    "logical",
    "conditional",
    "arithmetic",
    "map_values",
    "window_aggregate",
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


DERIVED_OPERATION_TYPES = {
    "derive_date_part",
    "bucketize",
    "compare",
    "logical",
    "conditional",
    "arithmetic",
    "map_values",
    "window_aggregate",
}


def order_derived_operations(
    operations: list[ProcessingOperation], selection: dict
) -> list[ProcessingOperation]:
    """행 단위 파생 operation을 컬럼 의존관계에 따라 안정적으로 위상 정렬한다."""

    source_names = {
        str(column.get("column"))
        for column in selection.get("source_columns") or []
        if isinstance(column, dict) and column.get("column")
    }
    source_names.update(
        str(column)
        for column in (selection.get("selection_query") or {}).get("columns") or []
    )
    derived = [operation for operation in operations if operation.type in DERIVED_OPERATION_TYPES]
    remaining = [operation for operation in operations if operation.type not in DERIVED_OPERATION_TYPES]
    if not derived:
        return list(operations)

    targets = [operation.target_column for operation in derived]
    if any(not target for target in targets):
        raise ProcessingPlanError("every derived operation requires target_column")
    if len(targets) != len(set(targets)):
        raise ProcessingPlanError("derived operation target columns must be unique")
    overwritten = source_names & set(targets)
    if overwritten:
        raise ProcessingPlanError(
            f"derived operations must not overwrite source columns: {', '.join(sorted(overwritten))}"
        )

    available = set(source_names)
    pending = list(derived)
    ordered: list[ProcessingOperation] = []
    while pending:
        executable = [
            operation
            for operation in pending
            if set(operation.source_columns).issubset(available)
        ]
        if not executable:
            pending_targets = {str(operation.target_column) for operation in pending}
            referenced = {
                column for operation in pending for column in operation.source_columns
            }
            unknown = referenced - available - pending_targets
            if unknown:
                raise ProcessingPlanError(
                    f"derived operations reference unknown columns: {', '.join(sorted(unknown))}"
                )
            raise ProcessingPlanError("derived operation dependency cycle detected")
        for operation in executable:
            pending.remove(operation)
            ordered.append(operation)
            available.add(str(operation.target_column))

    return [*ordered, *remaining]


def validate_approved_derived_coverage(
    operations: list[ProcessingOperation], selection: dict
) -> None:
    """승인된 파생 컬럼과 LLM 가공 계획의 생성 컬럼이 정확히 일치하는지 확인한다."""

    approved = {
        str(column.get("name"))
        for column in selection.get("derived_columns") or []
        if isinstance(column, dict) and column.get("name")
    }
    if not approved:
        return

    generated = {
        str(operation.target_column)
        for operation in operations
        if operation.type in DERIVED_OPERATION_TYPES and operation.target_column
    }
    for operation in operations:
        if operation.type not in {"aggregate", "window_aggregate"}:
            continue
        for metric in operation.parameters.get("metrics") or []:
            if isinstance(metric, dict) and metric.get("target"):
                generated.add(str(metric["target"]))

    missing = approved - generated
    unexpected = generated - approved
    if missing:
        raise ProcessingPlanError(
            f"processing plan does not create approved derived columns: {', '.join(sorted(missing))}"
        )
    if unexpected:
        raise ProcessingPlanError(
            f"processing plan creates unapproved derived columns: {', '.join(sorted(unexpected))}"
        )


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
                f"operation {operation.id} references unavailable columns: {', '.join(sorted(missing))}; "
                f"available_before_operation: {', '.join(sorted(available))}; "
                "move the operation that creates each missing target_column earlier, or remove the reference"
            )
        _validate_operation(operation)

        if operation.type in {"aggregate", "window_aggregate"}:
            field_prefix = operation.type
            group_by = _string_list(operation.parameters.get("group_by"), f"{field_prefix}.group_by")
            if set(group_by) - available:
                raise ProcessingPlanError(
                    f"{field_prefix} group_by references unavailable columns; "
                    "group_by may use only source columns or targets created by earlier operations"
                )
            metrics = operation.parameters.get("metrics")
            if not isinstance(metrics, list) or not metrics:
                raise ProcessingPlanError(f"{field_prefix}.metrics must be a non-empty list")
            metric_targets: set[str] = set()
            targets = set(group_by)
            for metric in metrics:
                if not isinstance(metric, dict):
                    raise ProcessingPlanError(f"{field_prefix} metric must be an object")
                column = str(metric.get("column") or "")
                function = str(metric.get("function") or "")
                target = str(metric.get("target") or "")
                if column not in available or function not in {"sum", "count", "count_distinct", "avg", "min", "max"} or not target:
                    raise ProcessingPlanError(f"{field_prefix} metric is invalid")
                targets.add(target)
                metric_targets.add(target)
            if operation.type == "window_aggregate":
                if not operation.target_column or operation.target_column not in metric_targets:
                    raise ProcessingPlanError(
                        "window_aggregate target_column must match one of its metric targets"
                    )
                available.update(metric_targets)
            else:
                available = targets
        elif operation.type == "select_columns":
            columns = _string_list(operation.parameters.get("columns"), "select_columns.columns")
            if set(columns) - available:
                raise ProcessingPlanError(
                    "select_columns references unavailable columns; "
                    "columns may use only source columns or targets created by earlier operations"
                )
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
        "derive_date_part": {"part", "timezone"},
        "bucketize": {"bins", "labels"},
        "compare": {"operator", "left", "right"},
        "logical": {"operator", "operands"},
        "conditional": {"condition", "true_value", "false_value"},
        "arithmetic": {"operator", "operands", "on_divide_by_zero"},
        "map_values": {"source", "mapping", "default"},
        "window_aggregate": {"group_by", "metrics"},
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
    if operation.type in {"compare", "logical", "conditional", "arithmetic", "map_values"}:
        if not operation.source_columns or not operation.target_column:
            raise ProcessingPlanError(f"{operation.type} requires source columns and target_column")
        referenced = _validate_expression_operation(operation.type, operation.parameters)
        if referenced != set(operation.source_columns):
            raise ProcessingPlanError(
                f"{operation.type} expression columns must exactly match source_columns"
            )
    if operation.type == "cast":
        data_type = operation.parameters.get("data_type")
        if not operation.source_columns or data_type not in {"string", "integer", "number", "date", "datetime"}:
            raise ProcessingPlanError("cast requires source columns and an allowed data_type")
    if operation.type == "fill_missing":
        strategy = operation.parameters.get("strategy")
        if not operation.source_columns or strategy not in {"median", "mode", "zero", "drop_row", "keep_null"}:
            raise ProcessingPlanError("fill_missing strategy is invalid")
    if operation.type == "derive_date_part" and operation.parameters.get("part") not in {"year", "month", "day", "weekday", "hour"}:
        raise ProcessingPlanError("derive_date_part.part is invalid")
    if operation.type == "derive_date_part" and operation.parameters.get("timezone", "UTC") not in {"UTC", "Asia/Seoul"}:
        raise ProcessingPlanError("derive_date_part.timezone is invalid")
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


def _validate_expression_operation(operation_type: str, parameters: dict[str, Any]) -> set[str]:
    if operation_type == "compare":
        if parameters.get("operator") not in {"eq", "neq", "gt", "gte", "lt", "lte", "in"}:
            raise ProcessingPlanError("compare.operator is invalid")
        return _validate_operand(parameters.get("left")) | _validate_operand(parameters.get("right"))
    if operation_type == "logical":
        operator = parameters.get("operator")
        operands = parameters.get("operands")
        if operator not in {"and", "or", "not"} or not isinstance(operands, list) or not operands:
            raise ProcessingPlanError("logical operation is invalid")
        if operator == "not" and len(operands) != 1:
            raise ProcessingPlanError("logical not requires exactly one operand")
        return set().union(*(_validate_operand(item) for item in operands))
    if operation_type == "conditional":
        return (
            _validate_operand(parameters.get("condition"))
            | _validate_operand(parameters.get("true_value"))
            | _validate_operand(parameters.get("false_value"))
        )
    if operation_type == "arithmetic":
        operator = parameters.get("operator")
        operands = parameters.get("operands")
        if operator not in {"add", "subtract", "multiply", "divide"}:
            raise ProcessingPlanError("arithmetic.operator is invalid")
        if not isinstance(operands, list) or len(operands) < 2:
            raise ProcessingPlanError("arithmetic.operands must contain at least two operands")
        if parameters.get("on_divide_by_zero", "null") not in {"null", "zero", "error"}:
            raise ProcessingPlanError("arithmetic.on_divide_by_zero is invalid")
        return set().union(*(_validate_operand(item) for item in operands))
    source = parameters.get("source")
    mapping = parameters.get("mapping")
    if not isinstance(mapping, dict):
        raise ProcessingPlanError("map_values.mapping must be an object")
    return _validate_operand(source) | _validate_operand(parameters.get("default", {"literal": None}))


def _validate_operand(value: Any) -> set[str]:
    if not isinstance(value, dict):
        raise ProcessingPlanError("expression operand must be an object")
    if set(value) == {"column"} and isinstance(value["column"], str) and value["column"]:
        return {value["column"]}
    if set(value) == {"literal"}:
        return set()
    if set(value) == {"operation", "parameters"} and isinstance(value.get("parameters"), dict):
        if value["operation"] not in {"compare", "logical", "conditional", "arithmetic", "map_values"}:
            raise ProcessingPlanError("nested expression operation is invalid")
        return _validate_expression_operation(value["operation"], value["parameters"])
    raise ProcessingPlanError("expression operand contract is invalid")
