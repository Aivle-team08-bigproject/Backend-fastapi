import json

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.supervisor_service import SupervisorService
from app.core.config import settings
from app.db.session import AsyncSessionLocal, get_db
from app.domain.enums import JobStatus
from app.schemas.supervisor import HitlReviewCreate, JobCreate, JobRead
from app.tasks.supervisor_tasks import run_supervisor_job_task

router = APIRouter(prefix="/supervisor", tags=["Automation Supervisor"])

_TERMINAL_JOB_STATUSES = {JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.WAITING_HITL.value}


@router.post("/jobs", response_model=JobRead, status_code=status.HTTP_201_CREATED)
async def create_job(payload: JobCreate, db: AsyncSession = Depends(get_db)):
    return await SupervisorService(db).create_job(
        raw_requirement=payload.raw_requirement,
        requirement_id=payload.requirement_id,
    )


@router.post("/jobs/{job_id}/run", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
async def run_job(job_id: int, db: AsyncSession = Depends(get_db)):
    job = await SupervisorService(db).get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    run_supervisor_job_task.delay(job_id)
    return job


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


async def _job_event_stream(job_id: int, snapshot: dict, is_terminal: bool):
    yield f"data: {json.dumps(snapshot, default=str)}\n\n"
    if is_terminal:
        return

    channel = f"events:job:{job_id}"
    client = redis.from_url(settings.redis_url)
    pubsub = client.pubsub()
    await pubsub.subscribe(channel)
    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0)
            if message is None:
                yield ": keep-alive\n\n"  # 프록시/브라우저가 idle 커넥션을 끊지 않도록
                continue
            event = json.loads(message["data"])
            yield f"data: {json.dumps(event, default=str)}\n\n"
            if event.get("job_status") in _TERMINAL_JOB_STATUSES:
                break
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        await client.aclose()


@router.get("/jobs/{job_id}/events")
async def stream_job_events(job_id: int):
    """PDF 설계의 '② 구독' 단계. Redis events:job:{id}를 구독해 SSE로 흘려보낸다.

    재접속 시 Last-Event-ID 기반 replay는 아직 없다(2번 단계에서 이미 밝힌 한계) —
    지금은 연결 시점의 현재 상태를 snapshot으로 한 번 보내고, 그 뒤로는 라이브
    이벤트만 이어붙인다. job이 이미 종료 상태면 snapshot만 보내고 바로 닫는다.
    """
    async with AsyncSessionLocal() as db:
        job = await SupervisorService(db).get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")

    snapshot = {
        "type": "job.snapshot",
        "job_id": job.id,
        "job_status": job.status,
        "current_stage": job.current_stage,
        "progress_percent": job.progress_percent,
        "stages": [
            {"stage_name": s.stage_name, "status": s.status}
            for s in sorted(job.stages, key=lambda s: s.run_order)
        ],
    }
    is_terminal = job.status in _TERMINAL_JOB_STATUSES

    return StreamingResponse(
        _job_event_stream(job_id, snapshot, is_terminal),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
