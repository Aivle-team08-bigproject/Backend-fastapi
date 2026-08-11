import asyncio

from app.core.config import settings
from app.domains.pipeline import service


def test_direct_backend_schedules_stage_runner_without_celery(monkeypatch):
    scheduled: list[int] = []

    async def fake_runner(run_id: int):
        scheduled.append(run_id)

    monkeypatch.setattr(settings, "pipeline_execution_backend", "AGENTCORE_DIRECT")
    monkeypatch.setattr("app.domains.pipeline.executor.run_pipeline_stage_direct", fake_runner)

    async def run():
        service._schedule_pipeline_execution(17, "execution-17")
        await asyncio.sleep(0)

    asyncio.run(run())
    assert scheduled == [17]
