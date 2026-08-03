"""Celery task — Supervisor dispatch와 단계 실행.

두 task로 나뉘어 있다:
- process_pipeline_run: Supervisor. 다음에 돌릴 단계를 정해서 run_pipeline_stage를 발행한다.
- run_pipeline_stage: 단계 하나를 실행하고 산출물을 검증한 뒤 승인 대기로 멈춘다.

상태 쓰기 주체는 이 Worker다 — status_recorder.record_status가 DB에 쓰고, 그 다음
프론트 화면 갱신용으로 Redis에 발행한다(FastAPI SSE가 구독).

process_pipeline_run은 run_id를 받아 다음 실행 단계를 발행한다.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from app.db.session import AsyncSessionLocal
from app.domains.pipeline.model import (
    PipelineRun,
    PipelineRunStatus,
    StageRun,
    StageRunStatus,
)
from app.domains.pipeline.supervisor import (
    STAGE_PROGRESS,
    StageDispatchError,
    StageName,
    build_stage_payload,
    next_pending_stage,
    rollback_target,
    run_stage,
)
from app.worker.celery_app import celery_app
from app.worker.status_recorder import record_status


def _run_async(coro):
    """task 본문의 async 코드를 실행한다.

    Celery worker 프로세스에는 돌고 있는 이벤트 루프가 없어 asyncio.run으로 충분하다.
    하지만 CELERY_TASK_ALWAYS_EAGER=true(브로커 없이 로컬에서 돌리는 모드)로 쓰면 task가
    FastAPI 요청 스레드 안에서 인라인 실행되는데, 그 스레드에는 이미 루프가 돌고 있어서
    asyncio.run이 RuntimeError로 터진다. 그 경우에만 별도 스레드에서 새 루프로 돌린다.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


async def _dispatch(run_id: int) -> dict:
    """다음 단계를 고르고 PENDING으로 기록한다.

    상태 이벤트의 celery_task_id는 항상 run의 dispatch id다(run.celery_task_id) — 단계
    task 자신의 id가 아니다. status_recorder가 이 값으로 stale 이벤트를 걸러내기 때문에
    두 task가 같은 값을 써야 한다.
    """
    async with AsyncSessionLocal() as db:
        run = await db.get(PipelineRun, run_id)
        if run is None:
            raise StageDispatchError(f"pipeline run not found: {run_id}")
        celery_task_id = run.celery_task_id or ""

        try:
            stage = await next_pending_stage(db, run_id)
        except StageDispatchError as exc:
            await record_status(
                db,
                run_id=run_id,
                celery_task_id=celery_task_id,
                run_status=PipelineRunStatus.FAILED,
                current_stage="DISPATCH",
                progress_percent=0,
                message="진행할 단계를 정할 수 없습니다.",
                error_message=str(exc),
            )
            raise

        stage_id, stage_code = stage.id, stage.stage_code
        await record_status(
            db,
            run_id=run_id,
            celery_task_id=celery_task_id,
            run_status=PipelineRunStatus.RUNNING,
            current_stage=stage_code,
            stage_status=StageRunStatus.PENDING,
            progress_percent=0,
            message=f"{stage_code} 단계를 준비했습니다.",
        )
        return {
            "stage_id": stage_id,
            "stage_code": stage_code,
            "celery_task_id": celery_task_id,
        }


async def _run_stage(stage_id: int, celery_task_id: str) -> dict:
    async with AsyncSessionLocal() as db:
        stage = await db.get(StageRun, stage_id)
        if stage is None:
            raise StageDispatchError(f"stage run not found: {stage_id}")
        run_id = stage.pipeline_run_id
        stage_name = StageName(stage.stage_code)

        await record_status(
            db,
            run_id=run_id,
            celery_task_id=celery_task_id,
            run_status=PipelineRunStatus.RUNNING,
            current_stage=stage_name.value,
            stage_status=StageRunStatus.RUNNING,
            progress_percent=max(STAGE_PROGRESS[stage_name] - 10, 0),
            message=f"{stage_name.value} 단계를 시작했습니다.",
        )
        # 모델 호출이 길어져도 PostgreSQL 트랜잭션이 열린 채 idle 상태로 남지 않게
        # 입력 payload를 미리 만들고 세션을 닫는다. 결과 저장은 호출 뒤 새 세션에서 한다.
        stage_payload = await build_stage_payload(db, stage)
        # rollback은 expire_on_commit=False인 세션에서도 ORM 객체를 만료시켜
        # 세션 밖에서 stage.stage_code를 읽을 때 DetachedInstanceError를 만든다.
        await db.commit()

    outcome = await run_stage(None, stage, payload=stage_payload)

    async with AsyncSessionLocal() as db:
        if not outcome["passed"]:
            failure_code = (outcome["validation"] or {}).get("failure_code")
            await record_status(
                db,
                run_id=run_id,
                celery_task_id=celery_task_id,
                run_status=PipelineRunStatus.FAILED,
                current_stage=stage_name.value,
                stage_status=StageRunStatus.FAILED,
                progress_percent=0,
                message=f"{stage_name.value} 산출물 검증에 실패했습니다.",
                result=outcome["output"],
                error_message=outcome["error_message"],
                validation_result=outcome["validation"],
                rollback_to_stage=rollback_target(failure_code).value,
            )
            return {"run_id": run_id, "stage_id": stage_id, "passed": False}

        result = dict(outcome["output"] or {})
        if outcome["artifact"]:
            result["artifact"] = outcome["artifact"]

        await record_status(
            db,
            run_id=run_id,
            celery_task_id=celery_task_id,
            run_status=outcome["run_status"],
            current_stage=stage_name.value,
            stage_status=StageRunStatus.COMPLETED,
            progress_percent=outcome["progress_percent"],
            message=f"{stage_name.value} 단계가 완료되어 검토를 기다립니다.",
            result=result,
            validation_result=outcome["validation"],
        )
        return {"run_id": run_id, "stage_id": stage_id, "passed": True}


@celery_app.task(bind=True, name="pipeline.process_run")
def process_pipeline_run(self, run_id: int) -> dict:
    """Supervisor — 다음 단계를 정해서 단계 worker를 발행한다."""
    dispatched = _run_async(_dispatch(run_id))
    run_pipeline_stage.apply_async(
        args=[dispatched["stage_id"], dispatched["celery_task_id"]]
    )
    return {"run_id": run_id, **dispatched}


@celery_app.task(name="pipeline.run_stage")
def run_pipeline_stage(stage_id: int, celery_task_id: str) -> dict:
    """단계 하나를 실행한다. 성공하면 해당 단계의 승인 대기 상태로 멈춘다.

    celery_task_id는 run의 dispatch id(run.celery_task_id)를 그대로 받는다 — 이 task 자신의
    id가 아니다. status_recorder의 stale 이벤트 판별 기준이라 dispatch와 같은 값이어야 한다.
    """
    return _run_async(_run_stage(stage_id, celery_task_id))
