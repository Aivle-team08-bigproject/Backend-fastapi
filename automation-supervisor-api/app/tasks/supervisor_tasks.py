import asyncio
import sys

from app.application.supervisor_service import SupervisorService
from app.core.celery_app import celery_app
from app.db.session import AsyncSessionLocal

if sys.platform == "win32":
    # psycopg의 async 모드는 Windows 기본 ProactorEventLoop를 못 쓴다 — Celery 워커
    # 프로세스가 각 태스크에서 새로 여는 이벤트 루프에도 이 정책이 필요하다.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@celery_app.task(name="supervisor.run_job")
def run_supervisor_job_task(job_id: int) -> int:
    """Supervisor 워커: job의 첫 스테이지를 등록하고 하위 워커(run_stage_task)에 넘긴다."""

    async def _run() -> int:
        async with AsyncSessionLocal() as session:
            stage = await SupervisorService(session).start_job(job_id)
            return stage.id

    stage_id = asyncio.run(_run())
    run_stage_task.delay(stage_id)
    return stage_id


@celery_app.task(name="supervisor.run_stage")
def run_stage_task(stage_run_id: int) -> int | None:
    """하위 Worker: 스테이지 하나를 실행하고, 다음 스테이지가 있으면 이어서 dispatch한다."""

    async def _run() -> int | None:
        async with AsyncSessionLocal() as session:
            _stage, next_stage = await SupervisorService(session).execute_stage(stage_run_id)
            return next_stage.id if next_stage else None

    next_stage_id = asyncio.run(_run())
    if next_stage_id is not None:
        run_stage_task.delay(next_stage_id)
    return next_stage_id
