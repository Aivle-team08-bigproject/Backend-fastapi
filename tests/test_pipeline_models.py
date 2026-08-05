from app.db import session as _service_session  # noqa: F401 - import registers all service models
from app.db.base import Base


def test_service_tables_are_registered_on_service_database_base():
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
        "admin_audit_logs",
        "employees",
        "employee_permissions",
        "login_sessions",
        "dashboard_alerts",
        "dashboard_insights",
        "system_dashboard_snapshots",
        "task_view_snapshots",
    }

    assert expected <= {table.name for table in Base.metadata.tables.values()}


def test_pipeline_run_has_unique_attempt_per_request():
    table = Base.metadata.tables["service.pipeline_runs"]
    assert any(
        constraint.name == "uq_pipeline_run_attempt"
        for constraint in table.constraints
    )
    assert "celery_task_id" in table.c
    assert table.c.celery_task_id.unique is True
