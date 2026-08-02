from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime.query.registry import DATASETS, resolve_column


class QueryPolicyError(ValueError):
    """Raised when an agent-produced selection plan violates data access policy."""


@dataclass(frozen=True)
class FilterCondition:
    column: str
    operator: str
    value: Any


@dataclass(frozen=True)
class SelectionPlan:
    datasets: tuple[str, ...]
    columns: tuple[str, ...]
    filters: tuple[FilterCondition, ...]
    limit: int

    @classmethod
    def from_agent_output(
        cls,
        selection: dict[str, Any],
        *,
        default_limit: int = 1000,
        max_limit: int = 50000,
    ) -> "SelectionPlan":
        selected = selection.get("selected_tables")
        if not isinstance(selected, list) or not selected:
            raise QueryPolicyError("selected_tables must be a non-empty list")
        datasets = tuple(dict.fromkeys(_dataset_name(item) for item in selected))
        unknown = [name for name in datasets if name not in DATASETS]
        if unknown:
            raise QueryPolicyError(f"unknown datasets: {', '.join(unknown)}")

        query = selection.get("selection_query") or {}
        if not isinstance(query, dict):
            raise QueryPolicyError("selection_query must be an object")
        raw_limit = query.get("limit", query.get("top_k", default_limit))
        if isinstance(raw_limit, bool):
            raise QueryPolicyError("query limit must be an integer")
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError) as exc:
            raise QueryPolicyError("query limit must be an integer") from exc
        if limit < 1 or limit > max_limit:
            raise QueryPolicyError(f"query limit must be between 1 and {max_limit}")

        allowed = set().union(*(DATASETS[name].allowed_columns for name in datasets))
        raw_columns = query.get("columns")
        if raw_columns is None:
            raw_columns = [
                name
                for dataset in datasets
                for name in DATASETS[dataset].default_columns
                if name in allowed
            ]
        if not isinstance(raw_columns, list):
            raise QueryPolicyError("query columns must be a list")
        columns = tuple(dict.fromkeys(resolve_column(str(name), allowed) for name in raw_columns))
        invalid_columns = [name for name in columns if name not in allowed]
        if invalid_columns:
            raise QueryPolicyError(f"columns are not allowed: {', '.join(invalid_columns)}")
        if not columns:
            raise QueryPolicyError("no allowed columns were selected")

        filters = _parse_filters(query.get("filters") or {}, allowed)
        return cls(datasets=datasets, columns=columns, filters=filters, limit=limit)


def _dataset_name(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict) and isinstance(item.get("table"), str):
        return item["table"]
    raise QueryPolicyError("each selected table must contain a table name")


def _parse_filters(raw: Any, allowed: set[str]) -> tuple[FilterCondition, ...]:
    if not isinstance(raw, dict):
        raise QueryPolicyError("selection_query.filters must be an object")
    conditions = []
    for raw_name, raw_value in raw.items():
        column = resolve_column(str(raw_name), allowed)
        if column not in allowed:
            raise QueryPolicyError(f"filter column is not allowed: {raw_name}")
        operator, value = _normalize_filter(raw_value)
        conditions.append(FilterCondition(column, operator, value))
    return tuple(conditions)


def _normalize_filter(raw: Any) -> tuple[str, Any]:
    if isinstance(raw, dict):
        operator = str(raw.get("operator", "eq")).lower()
        value = raw.get("value")
    elif isinstance(raw, (list, tuple, set)):
        operator, value = "in", list(raw)
    else:
        operator, value = "eq", raw
    if operator not in {"eq", "in", "gte", "lte", "between", "starts_with"}:
        raise QueryPolicyError(f"unsupported filter operator: {operator}")
    if operator in {"in", "between"} and not isinstance(value, (list, tuple)):
        raise QueryPolicyError(f"{operator} filter requires a list value")
    if operator == "between" and len(value) != 2:
        raise QueryPolicyError("between filter requires exactly two values")
    if operator == "starts_with" and not isinstance(value, str):
        raise QueryPolicyError("starts_with filter requires a string value")
    return operator, value
