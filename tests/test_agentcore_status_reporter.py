import asyncio
from datetime import datetime, timezone

from app.agentcore_status_reporter import AgentCoreStatusReporter
from app.domains.pipeline.model import PipelineRun, StageRun


class FakeSession:
    def __init__(self, run, stage):
        self.run = run
        self.stage = stage
        self.scalar_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def scalar(self, _statement):
        self.scalar_calls += 1
        return self.run if self.scalar_calls == 1 else self.stage


def _runtime_context():
    now = datetime.now(timezone.utc)
    run = PipelineRun(
        id=7,
        data_request_id=3,
        attempt_no=1,
        status="RUNNING",
        progress_percent=33,
        execution_id="exec-7",
        created_at=now,
        updated_at=now,
    )
    stage = StageRun(
        id=11,
        pipeline_run_id=7,
        stage_code="DATA_SELECTION",
        attempt_no=2,
        status="RUNNING",
        executor="AGENTCORE",
        input_payload={},
        output_payload={},
        validation_result={},
        created_at=now,
    )
    return run, stage


def test_runtime_selection_callback_persists_step_without_redis(monkeypatch):
    run, stage = _runtime_context()
    captured = {}

    async def fake_record_status(_db, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("app.agentcore_status_reporter.record_status", fake_record_status)
    reporter = AgentCoreStatusReporter(
        execution_id="exec-7",
        agent_name="data-selection-agent",
        session_factory=lambda: FakeSession(run, stage),
    )

    reporter.selection_step_callback(
        "SOURCE_COLUMN_SELECTION", "COMPLETED", {"column_count": 4}
    )

    assert captured["run_id"] == 7
    assert captured["execution_id"] == "exec-7"
    assert captured["attempt_no"] == 2
    assert captured["progress_percent"] == 44
    assert captured["publish"] is False
    assert captured["selection_step"].value == "SOURCE_COLUMN_SELECTION"
    assert captured["selection_step_status"].value == "COMPLETED"


def test_runtime_agent_log_persists_without_redis(monkeypatch):
    run, stage = _runtime_context()
    captured = {}

    async def fake_record_log(_db, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("app.agentcore_status_reporter.record_agent_log", fake_record_log)
    reporter = AgentCoreStatusReporter(
        execution_id="exec-7",
        agent_name="data-selection-agent",
        session_factory=lambda: FakeSession(run, stage),
    )

    reporter.agent_log_callback("INFO", "DB 메타데이터 확보", {"tables": 2})

    assert captured == {
        "run_id": 7,
        "execution_id": "exec-7",
        "message": "DB 메타데이터 확보",
        "level": "INFO",
        "current_stage": "DATA_SELECTION",
        "stage_run_id": 11,
        "detail": {"tables": 2},
        "publish": False,
    }


def test_runtime_callback_uses_runtime_event_loop(monkeypatch):
    run, stage = _runtime_context()
    captured = {}

    async def fake_record_status(_db, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("app.agentcore_status_reporter.record_status", fake_record_status)

    async def invoke_from_agent_thread():
        reporter = AgentCoreStatusReporter(
            execution_id="exec-7",
            agent_name="data-selection-agent",
            session_factory=lambda: FakeSession(run, stage),
            event_loop=asyncio.get_running_loop(),
        )
        await asyncio.to_thread(
            reporter.selection_step_callback,
            "SOURCE_COLUMN_SELECTION",
            "RUNNING",
            None,
        )

    asyncio.run(invoke_from_agent_thread())

    assert captured["publish"] is False
    assert captured["selection_step_status"].value == "RUNNING"
