from datetime import datetime
from decimal import Decimal

import pytest

from agent_runtime.query import DatabaseQueryExecutor, QueryPolicyError, SelectionPlan
from agent_runtime.query.executors import (
    PrivacyThresholdError,
    _enforce_minimum_group_size,
    _minimum_distinct_customers,
)
from agent_runtime.query.registry import DATASETS, K_ANONYMITY
from agent_runtime.query.metadata import canonical_dataset_names

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


def test_query_layer_coerces_datetime_between_values_before_binding():
    plan = SelectionPlan.from_agent_output(
        _selection(
            columns=["transaction_id"],
            filters={
                "transaction_datetime": {
                    "operator": "between",
                    "value": [
                        "2026-01-01T00:00:00+09:00",
                        "2026-06-30T23:59:59+09:00",
                    ],
                }
            },
        )
    )

    params = DatabaseQueryExecutor.build_statement(plan).compile().params
    date_values = [value for value in params.values() if isinstance(value, datetime)]

    assert len(date_values) == 2
    assert all(value.tzinfo is not None for value in date_values)


def test_query_layer_coerces_numeric_filter_values_before_binding():
    plan = SelectionPlan.from_agent_output(
        _selection(
            columns=["transaction_id"],
            filters={
                "mcc_code": {"operator": "in", "value": ["5812", "7011"]},
                "transaction_amount": {"operator": "gte", "value": "10000.50"},
            },
        )
    )

    params = DatabaseQueryExecutor.build_statement(plan).compile().params

    assert [5812, 7011] in params.values()
    assert Decimal("10000.50") in params.values()


def test_query_layer_rejects_invalid_datetime_before_database_execution():
    plan = SelectionPlan.from_agent_output(
        _selection(
            columns=["transaction_id"],
            filters={
                "transaction_datetime": {
                    "operator": "gte",
                    "value": "not-a-date",
                }
            },
        )
    )

    with pytest.raises(QueryPolicyError, match="invalid filter value for transaction_datetime"):
        DatabaseQueryExecutor.build_statement(plan)


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

    # 사람을 센다 — 행이 아니라
    assert "count(DISTINCT" in sql
    assert "customer_id" in sql
    assert f">= {K_ANONYMITY}" in sql
    # 출력 차원 전부로 그룹을 나눈다
    assert "GROUP BY" in sql
    # LIMIT은 세미조인 바깥 — 필터가 먼저 걸려야 한다
    assert sql.index("HAVING") < sql.rindex("LIMIT")


def test_rule_b_not_applied_to_single_person_attribute():
    """인적 속성 1개는 그 자체로 집단이므로 세미조인을 붙이지 않는다."""
    plan = SelectionPlan.from_agent_output(
        {
            "selected_tables": [{"table": "anon_customers"}],
            "selection_query": {"columns": ["customer_id", "age_band"]},
        }
    )
    assert "HAVING" not in str(DatabaseQueryExecutor.build_statement(plan))

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


def test_metadata_dataset_names_normalize_aliases_and_preserve_order():
    assert canonical_dataset_names(
        [
            "merchant",
            "anon_merchants",
            "transaction_pseudonymized",
            "member_pseudonymized",
        ]
    ) == ["anon_merchants", "anon_transactions", "anon_customers"]


def test_metadata_dataset_names_keep_canonical_defaults():
    assert canonical_dataset_names() == list(DATASETS)


def test_db_path_cannot_override_static_whitelist():
    """available_columns를 넘기면 화이트리스트가 통째로 대체된다.
    DB 전용 경로에서는 해당 인자를 받을 수 없어야 한다."""
    with pytest.raises(TypeError):
        SelectionPlan.from_agent_output(
            {"selected_tables": [{"table": "anon_customers"}]},
            available_columns={"gender", "age_band", "resident_region"},
        )

def test_high_cardinality_columns_are_identifiers():
    """값이 잘게 나뉘는 컬럼은 차원이 아니라 식별자로 막는다.

    차원으로 두면 규칙 B가 조용히 0행을 돌려주고, 에이전트는 그것을
    '데이터가 없다'로 잘못 해석한다. 실측(2026-07-31)에서
    transaction_datetime(2,730종)을 차원에 넣으면 통과 0행이었다.
    """
    # merchant_open_month는 2026-08-10 감사에서 화이트리스트 자체(blocked_columns)로
    # 옮겨졌다. 규칙 A는 인적 속성 2개 이상일 때만 발동해서 가맹점 단독 조회를 못 막기
    # 때문이다. 따라서 막히는 층이 컬럼마다 다르며, 어느 층이든 통과하면 안 된다.
    cases = [
        ("transaction_datetime", ["anon_transactions", "anon_customers"], "개별 식별자"),
        (
            "merchant_open_month",
            ["anon_merchants", "anon_customers", "anon_transactions"],
            "columns are not allowed",
        ),
        (
            "franchise_hq_code",
            ["anon_merchants", "anon_customers", "anon_transactions"],
            "개별 식별자",
        ),
    ]
    for column, tables, expected in cases:
        with pytest.raises(QueryPolicyError, match=expected):
            SelectionPlan.from_agent_output(
                {
                    "selected_tables": [{"table": t} for t in tables],
                    "selection_query": {"columns": ["gender", "age_band", column]},
                }
            )

def test_merchant_identity_columns_are_blocked_in_two_layers():
    """가맹점을 지목하는 값은 두 층에서 막힌다.

    merchant_id 는 화이트리스트를 통과하므로 규칙 A가 잡고,
    가맹점명·사업자번호는 화이트리스트 단계에서 이미 걸린다.
    셋은 서로 1:1이라 한 층만 막으면 우회된다.
    """
    with pytest.raises(QueryPolicyError, match="개별 식별자"):
        SelectionPlan.from_agent_output(
            {
                "selected_tables": [{"table": "anon_transactions"}, {"table": "anon_customers"}],
                "selection_query": {"columns": ["gender", "age_band", "merchant_id"]},
            }
        )

    for column in ("merchant_name", "business_registration_number"):
        with pytest.raises(QueryPolicyError, match="not allowed"):
            SelectionPlan.from_agent_output(
                {
                    "selected_tables": [{"table": "anon_merchants"}],
                    "selection_query": {"columns": [column]},
                }
            )

def test_rule_b_groups_by_all_output_dimensions():
    """판정 단위가 출력 단위와 같아야 한다.

    인적 속성만으로 그룹을 나누면 업종까지 포함된 행을 내보내면서
    안전성은 인적 속성 수준으로만 판단하게 된다.
    """
    plan = SelectionPlan.from_agent_output(
        {
            "selected_tables": [{"table": "anon_transactions"}, {"table": "anon_customers"}, {"table": "anon_mcc_codes"}],
            "selection_query": {"columns": ["gender", "age_band", "resident_region", "mcc_name"]},
        }
    )
    sql = str(DatabaseQueryExecutor.build_statement(plan).compile(
        compile_kwargs={"literal_binds": True}
    ))
    group_by = sql[sql.index("GROUP BY"):sql.index("HAVING")]
    for column in ("gender", "age_band", "resident_region", "mcc_name"):
        assert column in group_by


def test_k_anonymity_is_configurable():
    """데이터 규모가 커지면 K를 올릴 수 있어야 한다."""
    assert K_ANONYMITY >= 5
    assert isinstance(K_ANONYMITY, int)
