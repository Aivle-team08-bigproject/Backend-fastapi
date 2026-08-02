from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, Select, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.query.plan import FilterCondition, QueryPolicyError, SelectionPlan
from agent_runtime.query.registry import DATASETS, cards, customers, merchants, transactions


class DatabaseQueryExecutor:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def execute(self, selection: dict[str, Any]) -> list[dict[str, Any]]:
        plan = SelectionPlan.from_agent_output(selection)
        privacy_statement = self.build_privacy_count_statement(plan)
        if privacy_statement is not None:
            distinct_customers = int((await self.session.scalar(privacy_statement)) or 0)
            _enforce_minimum_group_size(
                distinct_customers,
                _minimum_distinct_customers(),
            )
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

    @staticmethod
    def build_privacy_count_statement(plan: SelectionPlan) -> Select | None:
        """실제 행 반환 전 동일 필터가 포함하는 고유 고객 수를 계산한다."""
        selected_tables = {DATASETS[name].table for name in plan.datasets}
        if transactions in selected_tables:
            source = transactions.join(
                cards,
                transactions.c.card_number_masked == cards.c.card_number_masked,
            ).join(
                customers,
                cards.c.customer_id == customers.c.customer_id,
            )
            available_tables = [transactions, cards, customers]
            if merchants in selected_tables:
                source = source.outerjoin(
                    merchants,
                    transactions.c.merchant_id == merchants.c.merchant_id,
                )
                available_tables.append(merchants)
        elif customers in selected_tables:
            source = customers
            available_tables = [customers]
        else:
            return None

        column_map = {
            column.name: column
            for table in available_tables
            for column in table.c
        }
        statement = select(func.count(distinct(customers.c.customer_id))).select_from(source)
        for condition in plan.filters:
            column = column_map.get(condition.column)
            if column is None:
                raise QueryPolicyError(
                    f"privacy filter column is unavailable: {condition.column}"
                )
            statement = statement.where(_sql_condition(column, condition))
        return statement


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
    if condition.operator == "starts_with":
        return column.startswith(condition.value, autoescape=True)
    raise QueryPolicyError(f"unsupported filter operator: {condition.operator}")


def _json_safe(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: float(value) if isinstance(value, Decimal) else value.isoformat() if hasattr(value, "isoformat") else value
        for key, value in row.items()
    }


def _minimum_distinct_customers() -> int:
    try:
        configured = int(os.getenv("DATA_PRIVACY_MIN_DISTINCT_CUSTOMERS", "5"))
    except ValueError as exc:
        raise QueryPolicyError(
            "DATA_PRIVACY_MIN_DISTINCT_CUSTOMERS must be an integer"
        ) from exc
    return max(5, configured)


def _enforce_minimum_group_size(actual: int, required: int) -> None:
    if actual < required:
        raise PrivacyThresholdError(
            f"privacy threshold not met: {actual} distinct customers; "
            f"at least {required} required"
        )


class PrivacyThresholdError(QueryPolicyError):
    """Raised before row retrieval when fewer than K distinct customers match."""
