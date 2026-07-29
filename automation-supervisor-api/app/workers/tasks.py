from typing import Any

from app.application.supervisor_service import SupervisorService
from app.db.session import AsyncSessionLocal


async def run_supervisor_worker(ctx: dict[str, Any], job_id: int) -> dict[str, Any]:
    """Decide and enqueue exactly one next stage Worker."""
    async with AsyncSessionLocal() as db:
        stage = await SupervisorService(db).dispatch_next_worker(job_id)

    queued = await ctx["redis"].enqueue_job("run_stage_worker", stage.id)
    if queued is None:
        raise RuntimeError(f"could not enqueue stage worker {stage.id}")
    return {"job_id": job_id, "stage_id": stage.id, "stage_name": stage.stage_name}


async def run_stage_worker(ctx: dict[str, Any], stage_id: int) -> dict[str, Any]:
    """Execute one stage Worker and persist its result."""
    async with AsyncSessionLocal() as db:
        job = await SupervisorService(db).run_worker(stage_id)
    return {
        "job_id": job.id,
        "stage_id": stage_id,
        "job_status": job.status,
        "current_stage": job.current_stage,
    }
