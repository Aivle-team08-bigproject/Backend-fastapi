from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, ColumnElement, Date, DateTime, Integer, Numeric, Select, distinct, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.query.plan import FilterCondition, QueryPolicyError, SelectionPlan
from agent_runtime.query.registry import (
    DATASETS,
    K_ANONYMITY,
    PERSON_ATTRIBUTES,
    QUASI_IDENTIFIERS,
    cards,
    customers,
    mcc_codes,
    merchants,
    transactions,
)

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
        column_map = {}
        for table in available_tables:
            for column in table.c:
                if column.name not in column_map:
                    column_map[column.name] = column

        columns = []
        for name in plan.columns:
            column = column_map.get(name)
            if column is None:
                raise QueryPolicyError(f"column is unavailable for selected join: {name}")
            columns.append(column)

        conditions = []
        for condition in plan.filters:
            column = column_map.get(condition.column)
            if column is None:
                raise QueryPolicyError(f"filter column is unavailable: {condition.column}")
            conditions.append(_sql_condition(column, condition))

        person_columns = [c for c in columns if c.name in PERSON_ATTRIBUTES]
        if len(person_columns) < 2:
            statement = select(*columns).select_from(source)
            for condition in conditions:
                statement = statement.where(condition)
            return statement.limit(plan.limit)

        # [규칙 B] 인적 속성이 2개 이상이면, 출력에 포함된 모든 차원 조합에
        # 서로 다른 고객이 K명 이상 있어야 그 행을 반환한다.
        #
        # 이전 구현은 두 가지가 틀렸다.
        #   1) count(*)가 행 수를 세서, 거래를 조인하면 한 사람이 여러 행이 되어
        #      고객 1명뿐인 조합도 통과했다.
        #   2) 판정 단위가 인적 속성뿐이라 출력 단위(업종 포함)보다 거칠었다.
        #      세밀하게 내보내면서 안전성은 뭉뚱그려 판단한 셈이다.
        #   실측: 12,477행 중 12,477행이 통과해 사실상 아무것도 막지 못했다.
        #        고객 1~2명인 조합 872개, 그중 1명뿐인 조합이 399개 새어나갔다.
        #
        # PostgreSQL은 window에서 COUNT(DISTINCT)를 지원하지 않으므로
        # GROUP BY + HAVING 서브쿼리로 안전한 조합을 먼저 구하고 세미조인한다.
        # 행 단위 출력이 유지되어 가공 단계가 받는 형태는 바뀌지 않는다.
        # LIMIT은 세미조인 바깥에 있어야 필터가 먼저 걸린다.
        dimension_columns = [c for c in columns if c.name in QUASI_IDENTIFIERS]
        customer_key = column_map.get("customer_id")
        if customer_key is None:
            raise QueryPolicyError(
                "인적 속성을 여러 개 조회하려면 고객 단위로 집단 크기를 셀 수 있어야 합니다. "
                "anon_customers를 함께 선택해 다시 요청하세요."
            )

        safe_combinations = select(*dimension_columns).select_from(source)
        for condition in conditions:
            safe_combinations = safe_combinations.where(condition)
        safe_combinations = (
            safe_combinations.group_by(*dimension_columns)
            .having(func.count(distinct(customer_key)) >= K_ANONYMITY)
            .subquery()
        )

        statement = select(*columns).select_from(source)
        for condition in conditions:
            statement = statement.where(condition)
        statement = statement.where(
            tuple_(*dimension_columns).in_(select(*safe_combinations.c))
        )
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
            if mcc_codes in selected_tables:
                source = source.join(
                    mcc_codes,
                    transactions.c.mcc_code == mcc_codes.c.mcc_code,
                )
                available_tables.append(mcc_codes)
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
        if mcc_codes in tables:
            # 거래의 mcc_code로 붙인다. 가맹점이 아니라 거래 기준이어야
            # 해외거래(merchant_id NULL)도 업종명을 얻는다.
            source = source.join(mcc_codes, transactions.c.mcc_code == mcc_codes.c.mcc_code)
            available.append(mcc_codes)
        if customers in tables:
            source = source.join(cards, transactions.c.card_number_masked == cards.c.card_number_masked)
            source = source.join(customers, cards.c.customer_id == customers.c.customer_id)
            available.extend([cards, customers])
        return source, available
    if mcc_codes in tables and merchants in tables and customers not in tables:
        source = merchants.join(mcc_codes, merchants.c.mcc_code == mcc_codes.c.mcc_code)
        return source, [merchants, mcc_codes]
    if customers in tables and (merchants in tables or mcc_codes in tables):
        raise QueryPolicyError(
            "고객 데이터와 가맹점/업종 데이터를 함께 조회하려면 anon_transactions가 필요합니다"
        )
    table = next(iter(tables))
    return table, [table]


def _sql_condition(column: ColumnElement, condition: FilterCondition):
    value = _coerce_filter_value(column, condition.value, condition.operator)
    if condition.operator == "eq":
        return column == value
    if condition.operator == "in":
        return column.in_(value)
    if condition.operator == "gte":
        return column >= value
    if condition.operator == "lte":
        return column <= value
    if condition.operator == "between":
        return column.between(value[0], value[1])
    if condition.operator == "starts_with":
        return column.startswith(value, autoescape=True)
    raise QueryPolicyError(f"unsupported filter operator: {condition.operator}")


def _coerce_filter_value(column: ColumnElement, value: Any, operator: str) -> Any:
    """Agent JSON 값을 실제 SQL 컬럼 타입으로 변환한다.

    JSON에는 날짜와 숫자가 문자열로 올 수 있다. 그대로 바인딩하면 PostgreSQL이
    ``timestamptz >= varchar`` 같은 비교를 거부하므로 SQL 생성 전에 변환한다.
    """
    values = list(value) if operator in {"in", "between"} else [value]
    try:
        converted = [_coerce_scalar(column, item) for item in values]
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise QueryPolicyError(
            f"invalid filter value for {column.name} ({column.type}): {value!r}"
        ) from exc
    return converted if operator in {"in", "between"} else converted[0]


def _coerce_scalar(column: ColumnElement, value: Any) -> Any:
    column_type = column.type
    if value is None:
        return None
    if isinstance(column_type, DateTime):
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if isinstance(column_type, Date):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value).strip())
    if isinstance(column_type, Boolean):
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
        raise ValueError("boolean value must be true or false")
    if isinstance(column_type, Integer):
        if isinstance(value, bool):
            raise ValueError("boolean is not an integer filter value")
        return int(value)
    if isinstance(column_type, Numeric):
        if isinstance(value, bool):
            raise ValueError("boolean is not a numeric filter value")
        return Decimal(str(value))
    return value


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
    # 0명과 "K명 미만"은 원인이 완전히 다르다. 하나로 묶으면 조건에 맞는 데이터가
    # 아예 없는 경우까지 "개인정보 보호 기준 미달"로 보고돼서, 실제로 재식별 가드
    # 탓으로 오진한 적이 두 번 있었다(요청 기간이 적재 범위 밖이거나, 지역×업종
    # 조합에 가맹점이 0개인 경우). 메시지와 실패 코드를 나눠야 원인이 바로 보인다.
    if actual == 0:
        raise EmptyResultError(
            "no rows match the requested filters; check the period, region, "
            "and category conditions against the available data"
        )
    if actual < required:
        raise PrivacyThresholdError(
            f"privacy threshold not met: {actual} distinct customers; "
            f"at least {required} required"
        )


class PrivacyThresholdError(QueryPolicyError):
    """Raised before row retrieval when fewer than K distinct customers match."""


class EmptyResultError(QueryPolicyError):
    """Raised when the filters match no rows at all — a data availability problem,
    not a privacy one."""
