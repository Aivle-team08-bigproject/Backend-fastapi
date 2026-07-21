from fastapi import APIRouter, Depends, HTTPException, status
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
    )


@router.post("/jobs/{job_id}/run", response_model=JobRead)
async def run_job(job_id: int, db: AsyncSession = Depends(get_db)):
    try:
        return await SupervisorService(db).run_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/hitl-review", response_model=JobRead)
async def submit_hitl_review(job_id: int, payload: HitlReviewCreate, db: AsyncSession = Depends(get_db)):
    try:
        return await SupervisorService(db).submit_hitl_review(
            job_id=job_id,
            approved=payload.approved,
            reviewer=payload.reviewer,
            natural_feedback=payload.natural_feedback,
            failure_code=payload.failure_code,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jobs/{job_id}", response_model=JobRead)
async def get_job(job_id: int, db: AsyncSession = Depends(get_db)):
    job = await SupervisorService(db).get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job
