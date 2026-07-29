import pytest

from agent_runtime.query import CsvQueryExecutor, DatabaseQueryExecutor, QueryPolicyError, SelectionPlan
from agent_runtime.query.registry import DATASETS, K_ANONYMITY

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

# ---------------------------------------------------------------------
# 재식별 방지 정책 (registry.py 1절 참고)
#   규칙 A  인적 속성 2개 이상 + 개별 식별자 동시 선택 금지
#   규칙 B  인적 속성 2개 이상이면 그룹 크기 K 미만인 행은 반환하지 않음
# ---------------------------------------------------------------------


def test_rule_a_rejects_identifier_with_multiple_person_attributes():
    """식별자와 함께 뽑으면 그룹 크기가 항상 1이 되어 규칙 B가 무력화된다."""
    with pytest.raises(QueryPolicyError, match="개별 식별자"):
        SelectionPlan.from_agent_output(
            {
                "selected_tables": [{"table": "anon_customers"}],
                "selection_query": {"columns": ["customer_id", "gender", "age_band"]},
            }
        )


def test_rule_a_allows_identifier_with_single_person_attribute():
    """인적 속성이 1개면 조합이 성립하지 않으므로 막지 않는다(과잉 차단 방지)."""
    plan = SelectionPlan.from_agent_output(
        {
            "selected_tables": [{"table": "anon_customers"}],
            "selection_query": {"columns": ["customer_id", "age_band"]},
        }
    )
    assert plan.columns == ("customer_id", "age_band")


def test_rule_b_adds_group_size_guard_for_quasi_identifiers():
    """준식별자 조합을 뽑으면 소규모 그룹이 SQL 단계에서 걸러져야 한다."""
    plan = SelectionPlan.from_agent_output(
        {
            "selected_tables": [{"table": "anon_customers"}],
            "selection_query": {"columns": ["gender", "age_band", "resident_region"]},
        }
    )
    sql = str(DatabaseQueryExecutor.build_statement(plan).compile(
        compile_kwargs={"literal_binds": True}
    ))

    assert "_group_size" in sql
    assert "PARTITION BY" in sql
    assert f">= {K_ANONYMITY}" in sql
    # LIMIT이 필터 바깥이어야 한다 — 안쪽이면 자르고 나서 세게 되어 결과가 어긋난다.
    assert sql.index("_group_size >=") < sql.rindex("LIMIT")


def test_rule_b_not_applied_to_single_person_attribute():
    """인적 속성 1개는 그 자체로 집단이므로 window를 붙이지 않는다."""
    plan = SelectionPlan.from_agent_output(
        {
            "selected_tables": [{"table": "anon_customers"}],
            "selection_query": {"columns": ["customer_id", "age_band"]},
        }
    )
    assert "_group_size" not in str(DatabaseQueryExecutor.build_statement(plan))


def test_default_columns_exclude_quasi_identifier_combination():
    """LLM이 columns를 생략해도 준식별자 조합이 자동으로 나가면 안 된다."""
    plan = SelectionPlan.from_agent_output(
        {"selected_tables": [{"table": "anon_customers"}]}
    )
    person_attrs = {"gender", "age_band", "resident_region", "postal_code",
                    "occupation", "annual_income_band", "marital_status"}
    assert len(person_attrs & set(plan.columns)) < 2


def test_mcc_dataset_joins_on_transaction_not_merchant():
    """가맹점 기준으로 조인하면 해외거래(merchant_id NULL)가 통째로 빠진다."""
    plan = SelectionPlan.from_agent_output(
        {
            "selected_tables": [{"table": "anon_transactions"}, {"table": "anon_mcc_codes"}],
            "selection_query": {"columns": ["transaction_id", "mcc_name"]},
        }
    )
    sql = " ".join(str(DatabaseQueryExecutor.build_statement(plan)).split())
    assert "anonymized.mcc_codes" in sql
    assert "anonymized.transactions.mcc_code = anonymized.mcc_codes.mcc_code" in sql


def test_legacy_dataset_names_resolve_to_anon_names():
    """팀 코드·프롬프트가 옛 논리명을 쓰므로 별칭이 유지되어야 한다."""
    plan = SelectionPlan.from_agent_output(
        {
            "selected_tables": [
                {"table": "transaction_pseudonymized"},
                {"table": "member_pseudonymized"},
            ],
            "selection_query": {"columns": ["transaction_id"], "top_k": 10},
        }
    )
    assert set(plan.datasets) == {"anon_transactions", "anon_customers"}
    assert all(name in DATASETS for name in plan.datasets)


def test_db_path_cannot_override_static_whitelist():
    """available_columns를 넘기면 화이트리스트가 통째로 대체된다.
    DB 경로에서는 그 인자를 넘길 수 없어야 한다(CSV 전용 진입점으로 분리)."""
    with pytest.raises(TypeError):
        SelectionPlan.from_agent_output(
            {"selected_tables": [{"table": "anon_customers"}]},
            available_columns={"gender", "age_band", "resident_region"},
        )