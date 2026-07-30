from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.supervisor_service import SupervisorService
from app.db.session import get_db
from app.schemas.supervisor import HitlReviewCreate, JobCreate, JobRead

router = APIRouter(prefix="/supervisor", tags=["Automation Supervisor"])


@router.post("/jobs", response_model=JobRead, status_code=status.HTTP_201_CREATED)
async def create_job(payload: JobCreate, db: AsyncSession = Depends(get_db)):
    return await SupervisorService(db).create_job(
        raw_requirement=payload.raw_requirement,
        requirement_id=payload.requirement_id,
        source_csv_path=payload.source_csv_path,
    )


@router.post("/jobs/{job_id}/run", response_model=JobRead)
async def run_job(job_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    service = SupervisorService(db)
    try:
        job = await service.mark_supervisor_queued(job_id)
        queued = await request.app.state.redis.enqueue_job("run_supervisor_worker", job_id)
        if queued is None:
            raise RuntimeError("Redis rejected the duplicate Supervisor Worker job")
        return job
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - restore a retryable DB state
        await service.mark_queue_failed(job_id, str(exc))
        raise HTTPException(status_code=503, detail="Supervisor Worker enqueue failed") from exc


@router.post("/jobs/{job_id}/hitl-review", response_model=JobRead)
async def submit_hitl_review(
    job_id: int,
    payload: HitlReviewCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    service = SupervisorService(db)
    try:
        job = await service.submit_hitl_review(
            job_id=job_id,
            approved=payload.approved,
            reviewer=payload.reviewer,
            natural_feedback=payload.natural_feedback,
            failure_code=payload.failure_code,
        )
        if not payload.approved or job.status == "COMPLETED":
            return job

        job = await service.mark_supervisor_queued(job_id)
        queued = await request.app.state.redis.enqueue_job("run_supervisor_worker", job_id)
        if queued is None:
            raise RuntimeError("Redis rejected the duplicate Supervisor Worker job")
        return job
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - restore a retryable DB state
        await service.mark_queue_failed(job_id, str(exc))
        raise HTTPException(status_code=503, detail="Supervisor Worker enqueue failed") from exc


@router.get("/jobs/{job_id}", response_model=JobRead)
async def get_job(job_id: int, db: AsyncSession = Depends(get_db)):
    job = await SupervisorService(db).get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job
