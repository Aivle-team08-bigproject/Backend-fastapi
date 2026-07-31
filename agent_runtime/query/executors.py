from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.query.plan import FilterCondition, QueryPolicyError, SelectionPlan
from agent_runtime.query.registry import DATASETS, cards, customers, merchants, transactions


class CsvQueryExecutor:
    def execute(self, rows: Iterable[Mapping[str, Any]], selection: dict[str, Any]) -> list[dict[str, Any]]:
        materialized = [dict(row) for row in rows]
        available = set().union(*(row.keys() for row in materialized)) if materialized else set()
        plan = SelectionPlan.from_agent_output(selection, available_columns=available)
        selected = []
        for row in materialized:
            if all(_matches(row.get(item.column), item) for item in plan.filters):
                selected.append({column: row.get(column) for column in plan.columns})
                if len(selected) >= plan.limit:
                    break
        return selected


class DatabaseQueryExecutor:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def execute(self, selection: dict[str, Any]) -> list[dict[str, Any]]:
        plan = SelectionPlan.from_agent_output(selection)
        statement = self.build_statement(plan)
        result = await self.session.execute(statement)
        return [_json_safe(dict(row)) for row in result.mappings().all()]

    @staticmethod
    def build_statement(plan: SelectionPlan) -> Select:
        source, available_tables = _joined_source(plan.datasets)
        columns = []
        column_map = {}
        for table in available_tables:
            for column in table.c:
                if column.name not in column_map:
                    column_map[column.name] = column
        for name in plan.columns:
            column = column_map.get(name)
            if column is None:
                raise QueryPolicyError(f"column is unavailable for selected join: {name}")
            columns.append(column)
        statement = select(*columns).select_from(source)
        for condition in plan.filters:
            column = column_map.get(condition.column)
            if column is None:
                raise QueryPolicyError(f"filter column is unavailable: {condition.column}")
            statement = statement.where(_sql_condition(column, condition))
        return statement.limit(plan.limit)


def _joined_source(dataset_names: tuple[str, ...]):
    tables = {DATASETS[name].table for name in dataset_names}
    if transactions in tables:
        source = transactions
        available = [transactions]
        if merchants in tables:
            source = source.outerjoin(merchants, transactions.c.merchant_id == merchants.c.merchant_id)
            available.append(merchants)
        if customers in tables:
            source = source.join(cards, transactions.c.card_number_masked == cards.c.card_number_masked)
            source = source.join(customers, cards.c.customer_id == customers.c.customer_id)
            available.extend([cards, customers])
        return source, available
    if customers in tables and merchants in tables:
        raise QueryPolicyError("member and merchant datasets require transaction_pseudonymized for a safe join")
    table = next(iter(tables))
    return table, [table]


def _sql_condition(column: ColumnElement, condition: FilterCondition):
    if condition.operator == "eq":
        return column == condition.value
    if condition.operator == "in":
        return column.in_(condition.value)
    if condition.operator == "gte":
        return column >= condition.value
    if condition.operator == "lte":
        return column <= condition.value
    if condition.operator == "between":
        return column.between(condition.value[0], condition.value[1])
    raise QueryPolicyError(f"unsupported filter operator: {condition.operator}")


def _matches(actual: Any, condition: FilterCondition) -> bool:
    expected = condition.value
    if condition.operator == "eq":
        return str(actual) == str(expected)
    if condition.operator == "in":
        return str(actual) in {str(value) for value in expected}
    try:
        comparable_actual, comparable_expected = _comparable(actual, expected)
        if condition.operator == "gte":
            return comparable_actual >= comparable_expected
        if condition.operator == "lte":
            return comparable_actual <= comparable_expected
        if condition.operator == "between":
            lower_actual, lower = _comparable(actual, expected[0])
            upper_actual, upper = _comparable(actual, expected[1])
            return lower <= lower_actual and upper_actual <= upper
    except (TypeError, ValueError):
        return False
    return False


def _comparable(actual: Any, expected: Any) -> tuple[Any, Any]:
    try:
        return Decimal(str(actual).replace(",", "")), Decimal(str(expected).replace(",", ""))
    except Exception:
        return str(actual), str(expected)


def _json_safe(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: float(value) if isinstance(value, Decimal) else value.isoformat() if hasattr(value, "isoformat") else value
        for key, value in row.items()
    }
