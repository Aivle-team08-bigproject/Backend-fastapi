import asyncio
import threading

from app.domains.pipeline.agent_client import AgentRuntimeClient


def test_execution_layer_log_callback_runs_off_event_loop_thread():
    loop_thread_id = threading.get_ident()
    callback_thread_ids = []

    def callback(level: str, message: str, detail: dict | None) -> None:
        callback_thread_ids.append(threading.get_ident())

    client = AgentRuntimeClient(agent_log_callback=callback)

    asyncio.run(asyncio.wait_for(client._log("INFO", "query started", {}), timeout=1))

    assert callback_thread_ids
    assert callback_thread_ids == [callback_thread_ids[0]]
    assert callback_thread_ids[0] != loop_thread_id


def test_unexpected_processing_executor_error_marks_step_failed(monkeypatch):
    from agent_runtime.data_processing import agent as processing_agent
    from agent_runtime.data_processing import planning_agent
    from agent_runtime.query import DatabaseQueryExecutor
    from app.db import hanacard_agent_session

    class FakeAgentSession:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    async def selected_rows(self, selection):
        return [{"region": "서울"}]

    def crash_executor(payload):
        raise RuntimeError("unexpected encoder error")

    monkeypatch.setattr(
        planning_agent,
        "create_processing_plan",
        lambda payload, on_step, on_log: {"plan_version": "1.0"},
    )
    monkeypatch.setattr(hanacard_agent_session, "AsyncSessionLocal", lambda: FakeAgentSession())
    monkeypatch.setattr(DatabaseQueryExecutor, "execute", selected_rows)
    monkeypatch.setattr(processing_agent, "run", crash_executor)

    steps = []
    client = AgentRuntimeClient(
        processing_step_callback=lambda code, status, metadata: steps.append(
            (code, status, metadata)
        )
    )

    result = asyncio.run(client._run_data_processing({"selection": {}}))

    assert result == {
        "_agent_error": "data processing executor failed: unexpected encoder error",
        "_failure_code": "PROCESSING_RULE_INVALID",
    }
    assert steps[-1] == (
        "DETERMINISTIC_PROCESSING",
        "FAILED",
        {"validation_errors": ["data processing executor failed: unexpected encoder error"]},
    )
