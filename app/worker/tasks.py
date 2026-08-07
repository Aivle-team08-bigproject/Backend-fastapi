"""Celery task — Supervisor dispatch와 단계 실행.

두 task로 나뉘어 있다:
- process_pipeline_run: Supervisor. 다음에 돌릴 단계를 정해서 run_pipeline_stage를 발행한다.
- run_pipeline_stage: 단계 하나를 실행하고 산출물을 검증한 뒤 승인 대기로 멈춘다.

상태 쓰기 주체는 이 Worker다 — status_recorder.record_status가 DB에 쓰고, 그 다음
프론트 화면 갱신용으로 Redis에 발행한다(FastAPI SSE가 구독).

process_pipeline_run은 run_id를 받아 다음 실행 단계를 발행한다.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from app.db.session import AsyncSessionLocal
from app.domains.pipeline.model import (
    AnalysisStepCode,
    AnalysisStepStatus,
    PipelineRun,
    PipelineRunStatus,
    ProcessingStepCode,
    ProcessingStepStatus,
    StageRun,
    StageRunStatus,
    SelectionStepCode,
    SelectionStepStatus,
)
from app.domains.pipeline.analysis_steps import (
    analysis_step_message,
    analysis_step_progress,
)
from app.domains.pipeline.selection_steps import (
    selection_step_message,
    selection_step_progress,
)
from app.domains.pipeline.processing_steps import (
    processing_step_message,
    processing_step_progress,
)
from app.domains.pipeline.supervisor import (
    STAGE_PROGRESS,
    StageDispatchError,
    StageName,
    next_pending_stage,
    rollback_target,
    run_stage,
)
from app.worker.celery_app import celery_app
from app.worker.status_recorder import record_agent_log, record_status


logger = logging.getLogger(__name__)


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
            attempt_no=stage.attempt_no
            if stage_name in {
                StageName.REQUIREMENT_ANALYSIS,
                StageName.DATA_SELECTION,
                StageName.DATA_PROCESSING,
            }
            else None,
            progress_percent=analysis_step_progress(
                AnalysisStepCode.REQUEST_ANALYSIS,
                AnalysisStepStatus.RUNNING,
            )
            if stage_name == StageName.REQUIREMENT_ANALYSIS
            else selection_step_progress(
                SelectionStepCode.SOURCE_COLUMN_SELECTION,
                SelectionStepStatus.RUNNING,
            )
            if stage_name == StageName.DATA_SELECTION
            else processing_step_progress(
                ProcessingStepCode.DEDUPLICATION_PLAN,
                ProcessingStepStatus.RUNNING,
            )
            if stage_name == StageName.DATA_PROCESSING
            else max(STAGE_PROGRESS[stage_name] - 10, 0),
            message=f"{stage_name.value} 단계를 시작했습니다.",
        )

        requirement_analysis_step_callback = None
        selection_step_callback = None
        processing_step_callback = None
        if stage_name == StageName.REQUIREMENT_ANALYSIS:
            loop = asyncio.get_running_loop()

            def requirement_analysis_step_callback(
                step_code: str, status_value: str, metadata: dict | None
            ) -> None:
                future = asyncio.run_coroutine_threadsafe(
                    _record_analysis_step(
                        db=db,
                        stage=stage,
                        celery_task_id=celery_task_id,
                        step=AnalysisStepCode(step_code),
                        status=AnalysisStepStatus(status_value),
                        metadata=metadata,
                    ),
                    loop,
                )
                future.result()

        if stage_name == StageName.DATA_SELECTION:
            loop = asyncio.get_running_loop()

            def selection_step_callback(
                step_code: str, status_value: str, metadata: dict | None
            ) -> None:
                future = asyncio.run_coroutine_threadsafe(
                    _record_selection_step(
                        db=db,
                        stage=stage,
                        celery_task_id=celery_task_id,
                        step=SelectionStepCode(step_code),
                        status=SelectionStepStatus(status_value),
                        metadata=metadata,
                    ),
                    loop,
                )
                future.result()

        if stage_name == StageName.DATA_PROCESSING:
            loop = asyncio.get_running_loop()

            def processing_step_callback(
                step_code: str, status_value: str, metadata: dict | None
            ) -> None:
                future = asyncio.run_coroutine_threadsafe(
                    _record_processing_step(
                        db=db,
                        stage=stage,
                        celery_task_id=celery_task_id,
                        step=ProcessingStepCode(step_code),
                        status=ProcessingStepStatus(status_value),
                        metadata=metadata,
                    ),
                    loop,
                )
                future.result()

        agent_log_loop = asyncio.get_running_loop()

        def agent_log_callback(level: str, message: str, detail: dict | None) -> None:
            """에이전트 기술 로그를 화면으로 흘려보낸다.

            상태 저장 경로와 달리 여기서 실패해도 단계 실행은 계속돼야 한다 — 관찰용
            로그 때문에 실제 작업이 죽으면 안 된다.
            """
            try:
                future = asyncio.run_coroutine_threadsafe(
                    record_agent_log(
                        db,
                        run_id=stage.pipeline_run_id,
                        celery_task_id=celery_task_id,
                        message=message,
                        level=level,
                        current_stage=stage.stage_code,
                        stage_run_id=stage.id,
                        detail=detail,
                    ),
                    agent_log_loop,
                )
                future.result()
            except Exception:  # noqa: BLE001 - 로깅 실패가 파이프라인을 멈추면 안 된다
                logger.exception("Failed to record agent log for run_id=%s", stage.pipeline_run_id)

        outcome = await run_stage(
            db,
            stage,
            requirement_analysis_step_callback=requirement_analysis_step_callback,
            selection_step_callback=selection_step_callback,
            processing_step_callback=processing_step_callback,
            agent_log_callback=agent_log_callback,
        )

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


async def _record_analysis_step(
    *,
    db,
    stage: StageRun,
    celery_task_id: str,
    step: AnalysisStepCode,
    status: AnalysisStepStatus,
    metadata: dict | None,
) -> None:
    """기존 StageRun snapshot에 요구사항 분석 서브스텝 상태를 기록한 뒤 이벤트를 발행한다."""
    if status not in {
        AnalysisStepStatus.RUNNING,
        AnalysisStepStatus.COMPLETED,
        AnalysisStepStatus.FAILED,
    }:
        raise ValueError(f"unsupported runtime checklist status: {status.value}")
    validation_errors = (metadata or {}).get("validation_errors") or []

    await record_status(
        db,
        run_id=stage.pipeline_run_id,
        celery_task_id=celery_task_id,
        run_status=PipelineRunStatus.RUNNING,
        current_stage=stage.stage_code,
        stage_status=StageRunStatus.RUNNING,
        analysis_step=step,
        analysis_step_status=status,
        attempt_no=stage.attempt_no,
        step_metadata=metadata,
        progress_percent=analysis_step_progress(step, status),
        message=analysis_step_message(step, status),
        error_message=validation_errors[0]
        if status == AnalysisStepStatus.FAILED and validation_errors
        else None,
    )


async def _record_selection_step(
    *,
    db,
    stage: StageRun,
    celery_task_id: str,
    step: SelectionStepCode,
    status: SelectionStepStatus,
    metadata: dict | None,
) -> None:
    """기존 StageRun snapshot에 상태를 기록한 뒤 이벤트를 발행한다."""
    message = selection_step_message(step, status)
    validation_errors = (metadata or {}).get("validation_errors") or []
    if status not in {
        SelectionStepStatus.RUNNING,
        SelectionStepStatus.COMPLETED,
        SelectionStepStatus.FAILED,
    }:
        raise ValueError(f"unsupported runtime checklist status: {status.value}")

    await record_status(
        db,
        run_id=stage.pipeline_run_id,
        celery_task_id=celery_task_id,
        run_status=PipelineRunStatus.RUNNING,
        current_stage=stage.stage_code,
        stage_status=StageRunStatus.RUNNING,
        selection_step=step,
        selection_step_status=status,
        attempt_no=stage.attempt_no,
        step_metadata=metadata,
        progress_percent=selection_step_progress(step, status),
        message=message,
        error_message=validation_errors[0]
        if status == SelectionStepStatus.FAILED and validation_errors
        else None,
    )


async def _record_processing_step(
    *,
    db,
    stage: StageRun,
    celery_task_id: str,
    step: ProcessingStepCode,
    status: ProcessingStepStatus,
    metadata: dict | None,
) -> None:
    """기존 StageRun processing snapshot에 계획 단계 상태를 기록한다."""
    if status not in {
        ProcessingStepStatus.RUNNING,
        ProcessingStepStatus.COMPLETED,
        ProcessingStepStatus.FAILED,
    }:
        raise ValueError(f"unsupported runtime processing status: {status.value}")
    validation_errors = (metadata or {}).get("validation_errors") or []
    await record_status(
        db,
        run_id=stage.pipeline_run_id,
        celery_task_id=celery_task_id,
        run_status=PipelineRunStatus.RUNNING,
        current_stage=stage.stage_code,
        stage_status=StageRunStatus.RUNNING,
        processing_step=step,
        processing_step_status=status,
        attempt_no=stage.attempt_no,
        step_metadata=metadata,
        progress_percent=processing_step_progress(step, status),
        message=processing_step_message(step, status),
        error_message=validation_errors[0]
        if status == ProcessingStepStatus.FAILED and validation_errors
        else None,
    )


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
