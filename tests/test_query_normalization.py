from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import Enum, Float

from agent_runtime.query.executors import DatabaseQueryExecutor
from agent_runtime.query.plan import SelectionPlan
from agent_runtime.query.registry import transactions
from agent_runtime.query.normalization import FilterValueError, normalize_filter_value


def test_datetime_filter_is_normalized_before_sql_binding():
    plan = SelectionPlan.from_agent_output(
        {
            "selected_tables": ["anon_transactions"],
            "selection_query": {
                "columns": ["transaction_datetime"],
                "filters": {
                    "transaction_datetime": {
                        "operator": "between",
                        "value": [
                            "2024-01-01 00:00:00+09:00",
                            "2024-06-30 23:59:59+09:00",
                        ],
                    }
                },
            },
        }
    )

    statement = DatabaseQueryExecutor.build_statement(plan)
    params = statement.compile().params
    datetime_values = [value for value in params.values() if isinstance(value, datetime)]

    assert datetime_values == [
        datetime(2023, 12, 31, 15, 0, tzinfo=timezone.utc),
        datetime(2024, 6, 30, 14, 59, 59, tzinfo=timezone.utc),
    ]


def test_normalize_common_scalar_types():
    assert normalize_filter_value(transactions.c.mcc_code.type, "5812") == 5812
    assert normalize_filter_value(transactions.c.transaction_amount.type, "12.50") == Decimal("12.50")
    assert normalize_filter_value(transactions.c.approval_status.type, 123) == "123"
    assert normalize_filter_value(Float(), "12.5") == 12.5
    assert normalize_filter_value(Enum("READY", "DONE"), "DONE") == "DONE"


def test_invalid_enum_is_rejected():
    with pytest.raises(FilterValueError, match="invalid enum value"):
        normalize_filter_value(Enum("READY", "DONE"), "UNKNOWN")


def test_invalid_datetime_is_rejected_before_sql_execution():
    with pytest.raises(FilterValueError, match="invalid datetime"):
        normalize_filter_value(transactions.c.transaction_datetime.type, "2024/01/01")
