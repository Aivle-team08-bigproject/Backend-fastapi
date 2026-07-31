import pytest

from agent_runtime.query import CsvQueryExecutor, DatabaseQueryExecutor, QueryPolicyError, SelectionPlan


def _selection(**query_overrides):
    return {
        "selected_tables": [{"table": "transaction_pseudonymized", "reason": "결제 분석"}],
        "selection_query": {"top_k": 20, "filters": {}, **query_overrides},
    }


def test_csv_executor_applies_validated_filters_columns_and_limit():
    rows = [
        {"customer_id": "u1", "region": "서울", "amount": "1000"},
        {"customer_id": "u2", "region": "부산", "amount": "2000"},
        {"customer_id": "u3", "region": "서울", "amount": "3000"},
    ]
    result = CsvQueryExecutor().execute(
        rows,
        _selection(columns=["customer_id", "region", "amount"], filters={"지역": "서울"}, top_k=1),
    )
    assert result == [{"customer_id": "u1", "region": "서울", "amount": "1000"}]


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
