from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime.query.registry import (
    DATASETS,
    PERSON_ATTRIBUTES,
    ROW_IDENTIFIERS,
    canonical_dataset,
    resolve_column,
)


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
        """운영 DB 조회용. 허용 컬럼은 정적 registry만 사용한다.

        available_columns를 아예 받지 않는다 — 이 인자가 넘어오면 정적
        화이트리스트가 통째로 대체되므로, DB 경로에서는 넘길 수 없어야 한다.
        """
        return cls._build(
            selection,
            default_limit=default_limit,
            max_limit=max_limit,
        )

    @classmethod
    def _build(
        cls,
        selection: dict[str, Any],
        *,
        default_limit: int,
        max_limit: int,
    ) -> "SelectionPlan":
        selected = selection.get("selected_tables")
        if not isinstance(selected, list) or not selected:
            raise QueryPolicyError("selected_tables must be a non-empty list")
        datasets = tuple(
            dict.fromkeys(canonical_dataset(_dataset_name(item)) for item in selected)
        )
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

        # [규칙 A] 인적 속성 2개 이상 + 개별 식별자 동시 선택 금지.
        # 함께 뽑으면 조합의 그룹 크기가 항상 1이 되어 규칙 B(executors.py)가
        # 무력화된다. 현재 조회 경로는 운영 DB 전용이므로 항상 적용한다.
        person_attrs = sorted(c for c in columns if c in PERSON_ATTRIBUTES)
        identifiers = sorted(c for c in columns if c in ROW_IDENTIFIERS)
        if len(person_attrs) >= 2 and identifiers:
            raise QueryPolicyError(
                "인적 속성 여러 개와 개별 식별자를 함께 선택할 수 없습니다. "
                f"인적속성={person_attrs}, 식별자={identifiers}. "
                "식별자를 빼고 집단 단위로 조회하거나, 인적 속성을 1개 이하로 줄이세요. "
                "거래시각·카드발급월·가맹점명처럼 값이 잘게 나뉘는 컬럼도 식별자로 취급합니다."
            )

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
