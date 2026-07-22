from app.db import session as _service_session  # noqa: F401 - import registers all service models
from app.db.base import Base


def test_demo_erd_tables_are_registered_on_service_database_base():
    expected = {
        "clients",
        "data_requests",
        "contracts",
        "contract_api_keys",
        "api_usage_logs",
        "pipeline_runs",
        "stage_runs",
        "pipeline_events",
        "agent_metrics",
        "artifacts",
        "reviews",
        "deliveries",
        "source_datasets",
        "source_customers",
        "source_cards",
        "source_merchants",
        "source_mcc_codes",
        "source_transactions",
    }

    assert expected <= set(Base.metadata.tables)


def test_pipeline_run_has_unique_attempt_per_request():
    table = Base.metadata.tables["pipeline_runs"]
    assert any(
        constraint.name == "uq_pipeline_run_attempt"
        for constraint in table.constraints
    )


def test_source_transactions_only_requires_dataset_reference():
    table = Base.metadata.tables["source_transactions"]
    foreign_key_targets = {foreign_key.target_fullname for foreign_key in table.foreign_keys}

    assert foreign_key_targets == {"source_datasets.id"}
