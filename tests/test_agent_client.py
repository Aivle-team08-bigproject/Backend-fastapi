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
