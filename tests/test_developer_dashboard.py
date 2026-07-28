from datetime import datetime, timezone

from app.domains.dashboard.schema import DeveloperDashboardPeriod
from app.domains.dashboard.service import _agent_status, _chart_buckets
from app.domains.pipeline.model import AgentMetric


def metric(*, outcome: str = "SUCCEEDED", latency_ms: int | None = 100) -> AgentMetric:
    return AgentMetric(
        stage_run_id=1,
        agent_name="test-agent",
        input_tokens=100,
        output_tokens=20,
        latency_ms=latency_ms,
        outcome=outcome,
    )


def test_daily_chart_uses_four_hour_buckets() -> None:
    start, buckets, size = _chart_buckets(
        DeveloperDashboardPeriod.DAILY,
        datetime(2026, 7, 23, 5, 30),
    )

    assert start == datetime(2026, 7, 22, 15, tzinfo=timezone.utc)
    assert [label for _, label in buckets] == ["00:00", "04:00", "08:00", "12:00", "16:00", "20:00"]
    assert size.total_seconds() == 4 * 60 * 60


def test_agent_status_uses_latest_outcome_and_latency() -> None:
    assert _agent_status(None) == "unknown"
    assert _agent_status(metric()) == "ok"
    assert _agent_status(metric(latency_ms=2_000)) == "delayed"
    assert _agent_status(metric(outcome="FAILED", latency_ms=None)) == "error"
