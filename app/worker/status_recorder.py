"""작업 상태 기록 — Worker가 DB에 직접 쓰고, 그 다음 프론트 화면 갱신용으로 발행한다.

쓰기 주체는 Worker다. Redis pub/sub은 영속화 경로가 아니라 화면 갱신 전용 통로다:

    Worker --(1) DB write--> PostgreSQL
           --(2) publish---> Redis --> FastAPI SSE --> 프론트 화면

(2)가 실패해도 상태는 이미 (1)에서 남아 있으므로 유실되지 않는다. 반대로 (1)이 실패하면
발행도 하지 않는다 — 화면에 DB에 없는 상태가 보이는 상황을 안 만든다.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.common.time_utils import utcnow
from app.domains.pipeline.model import (
    AnalysisStepStatus,
    Artifact,
    ArtifactType,
    DataRequest,
    DataRequestStatus,
    EventType,
    PipelineEvent,
    PipelineRun,
    PipelineRunStatus,
    PiiScanStatus,
    ProcessingStepStatus,
    SelectionStepStatus,
    StageRun,
    StageRunStatus,
)
from app.worker.status_event import PipelineStatusEvent
from app.domains.pipeline.failure import public_failure, public_step_metadata
from app.worker.status_publisher import publish_to_screen
from app.domains.pipeline.analysis_steps import initial_analysis_steps_snapshot
from app.domains.pipeline.selection_steps import initial_selection_steps_snapshot
from app.domains.pipeline.processing_steps import initial_processing_steps_snapshot


logger = logging.getLogger(__name__)


async def persist_status_event(db: AsyncSession, event: PipelineStatusEvent) -> bool:
    run = await db.get(PipelineRun, event.run_id)
    if run is None or run.execution_id != event.execution_id:
        logger.warning("Ignoring unknown or mismatched pipeline event for run_id=%s", event.run_id)
        return False

    now = event.occurred_at
    run.status = event.run_status.value
    run.current_stage = event.current_stage
    run.progress_percent = event.progress_percent
    run.updated_at = now
    if event.run_status == PipelineRunStatus.RUNNING and run.started_at is None:
        run.started_at = now
    if event.run_status in {PipelineRunStatus.COMPLETED, PipelineRunStatus.FAILED}:
        run.completed_at = now
    # Supervisor가 다음 dispatch에서 읽는 값들. 성공 이벤트에서는 지워서 이전 실패 흔적이
    # 남지 않게 한다.
    run.error_message = event.error_message
    run.rollback_to_stage = event.rollback_to_stage

    data_request = await db.get(DataRequest, run.data_request_id)
    if data_request is not None:
        if event.run_status == PipelineRunStatus.FAILED:
            data_request.status = DataRequestStatus.FAILED.value
        elif event.run_status == PipelineRunStatus.COMPLETED:
            data_request.status = DataRequestStatus.COMPLETED.value
        elif event.run_status == PipelineRunStatus.RUNNING:
            data_request.status = DataRequestStatus.RUNNING.value
        else:
            data_request.status = DataRequestStatus.WAITING_REVIEW.value
        data_request.updated_at = now

    stage = None
    if event.current_stage and event.stage_status is not None:
        # 롤백 재시도로 같은 stage_code가 여러 attempt 존재할 수 있으므로 최신 시도를 잡는다.
        stage = await db.scalar(
            select(StageRun)
            .where(
                StageRun.pipeline_run_id == run.id,
                StageRun.stage_code == event.current_stage,
            )
            .order_by(StageRun.attempt_no.desc(), StageRun.id.desc())
            .limit(1)
        )
        if stage is not None:
            event.stage_run_id = stage.id
            stage.status = event.stage_status.value
            stage.executor_reference = event.execution_id
            if event.current_stage == "REQUIREMENT_ANALYSIS":
                stage_payload = dict(stage.output_payload or {})
                steps = stage_payload.get("analysis_steps")
                if not isinstance(steps, dict):
                    steps = initial_analysis_steps_snapshot()
                if event.analysis_step is not None:
                    step_payload = dict(steps[event.analysis_step.value])
                    step_payload["status"] = event.analysis_step_status.value
                    if event.analysis_step_status == AnalysisStepStatus.RUNNING:
                        step_payload["started_at"] = (
                            step_payload.get("started_at") or now.isoformat()
                        )
                        step_payload["completed_at"] = None
                        step_payload["error_message"] = None
                    elif event.analysis_step_status == AnalysisStepStatus.COMPLETED:
                        step_payload["started_at"] = (
                            step_payload.get("started_at") or now.isoformat()
                        )
                        step_payload["completed_at"] = now.isoformat()
                        step_payload["error_message"] = None
                    elif event.analysis_step_status == AnalysisStepStatus.FAILED:
                        step_payload["started_at"] = (
                            step_payload.get("started_at") or now.isoformat()
                        )
                        step_payload["completed_at"] = now.isoformat()
                        step_payload["error_message"] = event.error_message
                    step_payload["metadata"] = event.step_metadata
                    steps[event.analysis_step.value] = step_payload
                stage_payload["analysis_steps"] = steps
                stage.output_payload = stage_payload
                flag_modified(stage, "output_payload")
            if event.current_stage == "DATA_SELECTION":
                stage_payload = dict(stage.output_payload or {})
                steps = stage_payload.get("selection_steps")
                if not isinstance(steps, dict):
                    steps = initial_selection_steps_snapshot()
                if event.selection_step is not None:
                    step_payload = dict(steps[event.selection_step.value])
                    step_payload["status"] = event.selection_step_status.value
                    if event.selection_step_status == SelectionStepStatus.RUNNING:
                        step_payload["started_at"] = (
                            step_payload.get("started_at") or now.isoformat()
                        )
                        step_payload["completed_at"] = None
                        step_payload["error_message"] = None
                    elif event.selection_step_status == SelectionStepStatus.COMPLETED:
                        step_payload["started_at"] = (
                            step_payload.get("started_at") or now.isoformat()
                        )
                        step_payload["completed_at"] = now.isoformat()
                        step_payload["error_message"] = None
                    elif event.selection_step_status == SelectionStepStatus.FAILED:
                        step_payload["started_at"] = (
                            step_payload.get("started_at") or now.isoformat()
                        )
                        step_payload["completed_at"] = now.isoformat()
                        step_payload["error_message"] = event.error_message
                    step_payload["metadata"] = event.step_metadata
                    steps[event.selection_step.value] = step_payload
                stage_payload["selection_steps"] = steps
                stage.output_payload = stage_payload
                flag_modified(stage, "output_payload")
            if event.current_stage == "DATA_PROCESSING":
                stage_payload = dict(stage.output_payload or {})
                stored_steps = stage_payload.get("processing_steps")
                if not isinstance(stored_steps, dict):
                    stored_steps = {}
                # 구버전 실행에는 계획 4단계만 저장돼 있을 수 있다. 현행 기본값을 먼저
                # 채우고 기존 상태를 덮어써서 신규 실행 단계 이벤트도 안전하게 기록한다.
                steps = dict(stored_steps)
                for code, default_step in initial_processing_steps_snapshot().items():
                    stored_step = stored_steps.get(code)
                    steps[code] = {
                        **default_step,
                        **(stored_step if isinstance(stored_step, dict) else {}),
                    }
                if event.processing_step is not None:
                    step_payload = dict(steps[event.processing_step.value])
                    step_payload["status"] = event.processing_step_status.value
                    if event.processing_step_status == ProcessingStepStatus.RUNNING:
                        step_payload["started_at"] = (
                            step_payload.get("started_at") or now.isoformat()
                        )
                        step_payload["completed_at"] = None
                        step_payload["error_message"] = None
                    elif event.processing_step_status == ProcessingStepStatus.COMPLETED:
                        step_payload["started_at"] = (
                            step_payload.get("started_at") or now.isoformat()
                        )
                        step_payload["completed_at"] = now.isoformat()
                        step_payload["error_message"] = None
                    elif event.processing_step_status == ProcessingStepStatus.FAILED:
                        step_payload["started_at"] = (
                            step_payload.get("started_at") or now.isoformat()
                        )
                        step_payload["completed_at"] = now.isoformat()
                        step_payload["error_message"] = event.error_message
                    step_payload["metadata"] = event.step_metadata
                    steps[event.processing_step.value] = step_payload
                stage_payload["processing_steps"] = steps
                stage.output_payload = stage_payload
                flag_modified(stage, "output_payload")
            if event.validation_result is not None:
                stage.validation_result = event.validation_result
            if event.stage_status == StageRunStatus.RUNNING:
                stage.started_at = stage.started_at or now
            elif event.stage_status == StageRunStatus.COMPLETED:
                result = dict(event.result or {})
                if event.current_stage == "REQUIREMENT_ANALYSIS":
                    result["analysis_steps"] = stage.output_payload.get(
                        "analysis_steps", initial_analysis_steps_snapshot()
                    )
                elif event.current_stage == "DATA_SELECTION":
                    result["selection_steps"] = stage.output_payload.get(
                        "selection_steps", initial_selection_steps_snapshot()
                    )
                elif event.current_stage == "DATA_PROCESSING":
                    result["processing_steps"] = stage.output_payload.get(
                        "processing_steps", initial_processing_steps_snapshot()
                    )
                stage.output_payload = result
                flag_modified(stage, "output_payload")
                stage.completed_at = now
            elif event.stage_status == StageRunStatus.FAILED:
                result = dict(event.result or {})
                if event.current_stage == "REQUIREMENT_ANALYSIS":
                    result["analysis_steps"] = stage.output_payload.get(
                        "analysis_steps", initial_analysis_steps_snapshot()
                    )
                elif event.current_stage == "DATA_SELECTION":
                    result["selection_steps"] = stage.output_payload.get(
                        "selection_steps", initial_selection_steps_snapshot()
                    )
                elif event.current_stage == "DATA_PROCESSING":
                    result["processing_steps"] = stage.output_payload.get(
                        "processing_steps", initial_processing_steps_snapshot()
                    )
                stage.output_payload = result
                flag_modified(stage, "output_payload")
                stage.error_message = event.error_message
                stage.completed_at = now

    is_failed_event = (
        event.run_status == PipelineRunStatus.FAILED
        or event.analysis_step_status == AnalysisStepStatus.FAILED
        or event.selection_step_status == SelectionStepStatus.FAILED
        or event.processing_step_status == ProcessingStepStatus.FAILED
    )
    pipeline_event = PipelineEvent(
        pipeline_run_id=run.id,
        stage_run_id=stage.id if stage else None,
        event_type=EventType.FAILED.value if is_failed_event else EventType.PROGRESS.value,
        severity="ERROR" if is_failed_event else "INFO",
        message=event.message,
        payload={
            "stage": event.current_stage,
            "status": event.run_status.value,
            "analysis_step": event.analysis_step.value
            if event.analysis_step
            else None,
            "analysis_step_status": event.analysis_step_status.value
            if event.analysis_step_status
            else None,
            "selection_step": event.selection_step.value
            if event.selection_step
            else None,
            "selection_step_status": event.selection_step_status.value
            if event.selection_step_status
            else None,
            "processing_step": event.processing_step.value
            if event.processing_step
            else None,
            "processing_step_status": event.processing_step_status.value
            if event.processing_step_status
            else None,
            "attempt_no": event.attempt_no,
            "stage_run_id": event.stage_run_id,
            "step_metadata": public_step_metadata(event.step_metadata),
            "progress_percent": event.progress_percent,
            # 상세 산출물과 실패 후보 계획은 StageRun에만 저장한다.
            "error_message": event.error_message,
            "failure": event.failure,
            "rollback_to_stage": event.rollback_to_stage,
        },
        occurred_at=now,
    )
    db.add(pipeline_event)
    # Redis/SSE에 DB 이벤트의 실제 PK를 넣기 위해 commit 전에 ID를 확정한다.
    await db.flush()
    event.event_id = pipeline_event.id
    # HITL 도입 후 가공 단계가 끝나면 run은 COMPLETED가 아니라 WAITING_FINAL_REVIEW로
    # 멈춘다. 그래도 결과 파일은 이미 저장돼 있으므로 상태와 무관하게 Artifact를 남긴다
    # (storage_key 중복 검사로 멱등).
    artifact_payload = (event.result or {}).get("artifact")
    if artifact_payload:
        existing = await db.scalar(
            select(Artifact).where(Artifact.storage_key == artifact_payload["storage_key"])
        )
        if existing is None:
            db.add(
                Artifact(
                    pipeline_run_id=run.id,
                    stage_run_id=stage.id if stage else None,
                    artifact_type=ArtifactType.FINAL.value,
                    storage_key=artifact_payload["storage_key"],
                    mime_type=artifact_payload.get("mime_type", "text/csv"),
                    size_bytes=artifact_payload.get("size_bytes"),
                    checksum=artifact_payload.get("checksum"),
                    pii_scan_status=PiiScanStatus.PASSED.value,
                    created_at=now,
                )
            )
    await db.commit()
    return True


async def record_agent_log(
    db: AsyncSession,
    *,
    run_id: int,
    execution_id: str,
    message: str,
    level: str = "INFO",
    current_stage: str | None = None,
    stage_run_id: int | None = None,
    detail: dict | None = None,
) -> PipelineStatusEvent | None:
    """에이전트 내부 기술 로그를 남긴다 — 상태 전이가 아니라 관찰 기록이다.

    record_status와 달리 PipelineRun/StageRun을 일절 건드리지 않는다. 진행률이나 단계
    상태는 그대로 두고 pipeline_events에 한 줄 남긴 뒤 화면 로그 패널로 발행만 한다.
    재시도 사유처럼 "실패는 아니지만 실무자가 알아야 하는 일"이 이 경로로 나간다.
    """
    run = await db.get(PipelineRun, run_id)
    if run is None or run.execution_id != execution_id:
        logger.warning("Ignoring agent log for unknown or mismatched run_id=%s", run_id)
        return None

    now = utcnow()
    pipeline_event = PipelineEvent(
        pipeline_run_id=run.id,
        stage_run_id=stage_run_id,
        event_type=EventType.AGENT_LOG.value,
        severity=level,
        message=message,
        payload={
            "stage": current_stage,
            "log_level": level,
            "detail": detail,
        },
        occurred_at=now,
    )
    db.add(pipeline_event)
    await db.flush()

    event = PipelineStatusEvent(
        event_id=pipeline_event.id,
        run_id=run.id,
        execution_id=execution_id,
        event_kind="agent_log",
        log_level=level,
        # 로그는 상태를 바꾸지 않으므로 현재 값을 그대로 실어 보낸다.
        run_status=PipelineRunStatus(run.status),
        current_stage=current_stage or run.current_stage,
        stage_run_id=stage_run_id,
        step_metadata=detail,
        progress_percent=run.progress_percent,
        message=message,
        occurred_at=now,
    )
    await db.commit()
    publish_to_screen(event)
    return event


async def record_status(
    db: AsyncSession,
    *,
    run_id: int,
    execution_id: str,
    run_status: PipelineRunStatus,
    progress_percent: int,
    message: str,
    current_stage: str | None = None,
    stage_status: StageRunStatus | None = None,
    result: dict | None = None,
    error_message: str | None = None,
    validation_result: dict | None = None,
    rollback_to_stage: str | None = None,
    analysis_step=None,
    analysis_step_status=None,
    selection_step=None,
    selection_step_status=None,
    processing_step=None,
    processing_step_status=None,
    attempt_no: int | None = None,
    step_metadata: dict | None = None,
) -> PipelineStatusEvent | None:
    """상태를 DB에 쓰고, 성공하면 화면 갱신용으로 발행한다.

    run을 못 찾거나 execution_id가 안 맞으면 아무것도 발행하지 않고 None을 돌려준다.
    """
    failure = public_failure(
        stage=current_stage,
        result=result,
        validation_result=validation_result,
        rollback_to_stage=rollback_to_stage,
        error_message=error_message,
    )
    event = PipelineStatusEvent(
        run_id=run_id,
        execution_id=execution_id,
        run_status=run_status,
        current_stage=current_stage,
        stage_status=stage_status,
        analysis_step=analysis_step,
        analysis_step_status=analysis_step_status,
        selection_step=selection_step,
        selection_step_status=selection_step_status,
        processing_step=processing_step,
        processing_step_status=processing_step_status,
        attempt_no=attempt_no,
        step_metadata=step_metadata,
        progress_percent=progress_percent,
        message=message,
        result=result,
        error_message=error_message,
        validation_result=validation_result,
        rollback_to_stage=rollback_to_stage,
        failure=failure,
        occurred_at=utcnow(),
    )
    if not await persist_status_event(db, event):
        return None
    publish_to_screen(event)
    return event
