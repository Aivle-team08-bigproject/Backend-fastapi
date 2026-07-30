import asyncio
import os

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.supervisor_service import SupervisorService
from app.db.session import AsyncSessionLocal
def _pending_worker_id(job) -> int | None:
    pending = [stage for stage in job.stages if stage.status == "PENDING"]
    return max(pending, key=lambda stage: stage.run_order).id if pending else None


async def _handle(event: dict, db: AsyncSession) -> dict:
    service = SupervisorService(db)
    action = event.get("action", "run_job")

    if action == "run_job":
        stage = await service.dispatch_next_worker(int(event["job_id"]))
        job = await service.get_job(stage.job_id)
        return {
            "job_id": job.id,
            "status": job.status,
            "current_stage": job.current_stage,
            "worker_id": _pending_worker_id(job),
        }

    if action == "run_worker":
        job = await service.run_worker(int(event["stage_id"]))
        return {"job_id": job.id, "status": job.status, "current_stage": job.current_stage}

    if action == "submit_hitl_review":
        job = await service.submit_hitl_review(
            job_id=int(event["job_id"]),
            approved=bool(event["approved"]),
            reviewer=event["reviewer"],
            natural_feedback=event.get("natural_feedback", ""),
            failure_code=event.get("failure_code"),
        )
        return {
            "job_id": job.id,
            "status": job.status,
            "rollback_to_stage": job.rollback_to_stage,
            "worker_id": _pending_worker_id(job),
        }

    raise ValueError(f"unsupported action: {action}")


def lambda_handler(event, context):
    os.environ.setdefault("AWS_LAMBDA_FUNCTION_NAME", getattr(context, "function_name", "local"))

    async def runner():
        async with AsyncSessionLocal() as db:
            return await _handle(event, db)

    return asyncio.run(runner())
