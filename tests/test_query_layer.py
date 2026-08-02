import pytest

from agent_runtime.query import DatabaseQueryExecutor, QueryPolicyError, SelectionPlan
from agent_runtime.query.executors import (
    PrivacyThresholdError,
    _enforce_minimum_group_size,
    _minimum_distinct_customers,
)


def _selection(**query_overrides):
    return {
        "selected_tables": [{"table": "transaction_pseudonymized", "reason": "결제 분석"}],
        "selection_query": {"top_k": 20, "filters": {}, **query_overrides},
    }


def test_query_plan_rejects_unknown_dataset_and_excessive_limit():
    with pytest.raises(QueryPolicyError, match="unknown datasets"):
        SelectionPlan.from_agent_output(
            {
                "selected_tables": [{"table": "mart.secret_table"}],
                "selection_query": {"top_k": 10},
            }
        )
    with pytest.raises(QueryPolicyError, match="between 1 and 50000"):
        SelectionPlan.from_agent_output(_selection(top_k=50001))


def test_database_plan_blocks_sensitive_columns():
    with pytest.raises(QueryPolicyError, match="not allowed"):
        SelectionPlan.from_agent_output(
            _selection(columns=["transaction_id", "card_number_masked"])
        )


def test_database_executor_builds_bound_safe_join_query():
    selection = {
        "selected_tables": [
            {"table": "transaction_pseudonymized"},
            {"table": "merchant"},
        ],
        "selection_query": {
            "columns": ["transaction_id", "merchant_region", "transaction_amount"],
            "filters": {"지역": "서울"},
            "top_k": 100,
        },
    }
    plan = SelectionPlan.from_agent_output(selection)
    statement = DatabaseQueryExecutor.build_statement(plan)
    sql = str(statement)

    assert "anonymized.transactions" in sql
    assert "anonymized.merchants" in sql
    assert "서울" not in sql
    assert statement.compile().params


def test_query_plan_accepts_comment_evidence_in_filter_contract():
    selection = {
        "selected_tables": [{"table": "member_pseudonymized"}],
        "selection_query": {
            "columns": ["customer_id", "age_band"],
            "filters": {
                "age_band": {
                    "operator": "in",
                    "value": ["30대"],
                    "reason": "사용자가 30대 고객을 요청함",
                    "evidence": "연령 구간을 나타내는 컬럼 COMMENT",
                }
            },
        },
    }

    plan = SelectionPlan.from_agent_output(selection)

    assert plan.filters[0].column == "age_band"
    assert plan.filters[0].operator == "in"
    assert plan.filters[0].value == ["30대"]


def test_database_executor_builds_bound_starts_with_filter():
    selection = {
        "selected_tables": [{"table": "member_pseudonymized"}],
        "selection_query": {
            "columns": ["customer_id", "resident_region"],
            "filters": {
                "resident_region": {
                    "operator": "starts_with",
                    "value": "서울특별시",
                    "reason": "현재 데이터에서 수도권에 가장 가까운 서울 범위",
                    "evidence": "서울특별시와 자치구 형태라는 컬럼 COMMENT",
                }
            },
        },
    }

    plan = SelectionPlan.from_agent_output(selection)
    statement = DatabaseQueryExecutor.build_statement(plan)

    assert "서울특별시" not in str(statement)
    assert statement.compile().params


def test_privacy_count_uses_distinct_customer_before_row_query():
    selection = {
        "selected_tables": [
            {"table": "member_pseudonymized"},
            {"table": "transaction_pseudonymized"},
        ],
        "selection_query": {
            "columns": ["age_band", "mcc_code"],
            "filters": {
                "age_band": {"operator": "eq", "value": "30대"},
                "mcc_code": {"operator": "in", "value": [4722, 7011]},
            },
        },
    }
    plan = SelectionPlan.from_agent_output(selection)

    statement = DatabaseQueryExecutor.build_privacy_count_statement(plan)
    sql = str(statement)

    assert "count(DISTINCT" in sql
    assert "anonymized.cards" in sql
    assert "anonymized.customers" in sql
    assert "30대" not in sql
    assert statement.compile().params


def test_privacy_threshold_accepts_five_and_rejects_four(monkeypatch):
    monkeypatch.setenv("DATA_PRIVACY_MIN_DISTINCT_CUSTOMERS", "3")

    assert _minimum_distinct_customers() == 5
    _enforce_minimum_group_size(5, 5)
    with pytest.raises(PrivacyThresholdError, match="4 distinct customers"):
        _enforce_minimum_group_size(4, 5)
