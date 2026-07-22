import asyncio
import os

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.supervisor_service import SupervisorService
from app.db.session import AsyncSessionLocal
from app.domain.enums import StageName


async def _handle(event: dict, db: AsyncSession) -> dict:
    service = SupervisorService(db)
    action = event.get("action", "run_job")

    if action == "run_job":
        job = await service.run_job(int(event["job_id"]))
        return {"job_id": job.id, "status": job.status, "current_stage": job.current_stage}

    if action == "run_stage":
        job = await service.get_job(int(event["job_id"]))
        if job is None:
            raise ValueError("job not found")
        stage = await service.run_stage(
            job=job,
            stage_name=StageName(event["stage_name"]),
            previous_artifacts=event.get("previous_artifacts", {}),
        )
        await db.commit()
        return {
            "job_id": job.id,
            "stage_name": stage.stage_name,
            "status": stage.status,
            "validation_result": stage.validation_result,
        }

    if action == "submit_hitl_review":
        job = await service.submit_hitl_review(
            job_id=int(event["job_id"]),
            approved=bool(event["approved"]),
            reviewer=event["reviewer"],
            natural_feedback=event.get("natural_feedback", ""),
            failure_code=event.get("failure_code"),
        )
        return {"job_id": job.id, "status": job.status, "rollback_to_stage": job.rollback_to_stage}

    raise ValueError(f"unsupported action: {action}")


def lambda_handler(event, context):
    os.environ.setdefault("AWS_LAMBDA_FUNCTION_NAME", getattr(context, "function_name", "local"))

    async def runner():
        async with AsyncSessionLocal() as db:
            return await _handle(event, db)

    return asyncio.run(runner())
