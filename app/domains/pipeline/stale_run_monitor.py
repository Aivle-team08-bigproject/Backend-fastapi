"""고아 pipeline 실행 감시 및 제한적 자동 복구."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select

from app.common.time_utils import utcnow
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.domains.pipeline.model import (
    DataRequest,
    DataRequestStatus,
    EventType,
    PipelineEvent,
    PipelineRun,
    PipelineRunStatus,
    StageRun,
    StageRunStatus,
)
from app.domains.pipeline.service import _redispatch, _reopen_from
from app.domains.pipeline.supervisor import StageName

logger = logging.getLogger(__name__)


async def _recover_stale_run(run_id: int, cutoff) -> None:
    async with AsyncSessionLocal() as db:
        run = await db.scalar(select(PipelineRun).where(PipelineRun.id == run_id))
        if run is None or run.status != PipelineRunStatus.RUNNING.value or run.updated_at >= cutoff:
            return
        stage = await db.scalar(
            select(StageRun)
            .where(StageRun.pipeline_run_id == run.id, StageRun.status == StageRunStatus.RUNNING.value)
            .order_by(StageRun.attempt_no.desc(), StageRun.id.desc())
            .limit(1)
        )
        if stage is None:
            return

        now = utcnow()
        reason = f"{stage.stage_code} 실행이 {settings.pipeline_stale_run_after_seconds}초 동안 갱신되지 않았습니다."
        if stage.attempt_no < settings.pipeline_stale_auto_recovery_max_attempts:
            await _reopen_from(db, run, StageName(stage.stage_code), now)
            run.status = PipelineRunStatus.QUEUED.value
            run.current_stage = stage.stage_code
            run.error_message = reason
            run.rollback_to_stage = stage.stage_code
            run.updated_at = now
            db.add(PipelineEvent(
                pipeline_run_id=run.id, stage_run_id=stage.id,
                event_type=EventType.PROGRESS.value, severity="WARN",
                message=f"고아 실행을 감지해 {stage.stage_code} 단계를 자동 재시도합니다.",
                payload={"reason": reason, "automatic_recovery": True, "attempt_no": stage.attempt_no},
                occurred_at=now,
            ))
            await _redispatch(db, run, now)
            logger.warning("Recovered stale pipeline run_id=%s stage=%s attempt=%s", run.id, stage.stage_code, stage.attempt_no)
            return

        stage.status = StageRunStatus.FAILED.value
        stage.error_message = reason
        stage.completed_at = now
        run.status = PipelineRunStatus.FAILED.value
        run.current_stage = stage.stage_code
        run.error_message = f"자동 복구 횟수를 초과했습니다. {reason}"
        run.completed_at = now
        run.updated_at = now
        request = await db.get(DataRequest, run.data_request_id)
        if request is not None:
            request.status = DataRequestStatus.FAILED.value
            request.updated_at = now
        db.add(PipelineEvent(
            pipeline_run_id=run.id, stage_run_id=stage.id,
            event_type=EventType.FAILED.value, severity="ERROR",
            message="고아 실행 자동 복구 한도를 초과해 작업을 종료했습니다.",
            payload={"reason": reason, "automatic_recovery": False, "attempt_no": stage.attempt_no},
            occurred_at=now,
        ))
        await db.commit()
        logger.error("Stale pipeline run exhausted recovery run_id=%s stage=%s", run.id, stage.stage_code)


async def monitor_stale_pipeline_runs(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            cutoff = utcnow() - timedelta(seconds=settings.pipeline_stale_run_after_seconds)
            async with AsyncSessionLocal() as db:
                run_ids = list((await db.scalars(select(PipelineRun.id).where(
                    PipelineRun.status == PipelineRunStatus.RUNNING.value,
                    PipelineRun.updated_at < cutoff,
                ))).all())
            for run_id in run_ids:
                await _recover_stale_run(run_id, cutoff)
        except Exception:
            logger.exception("stale pipeline monitor failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.pipeline_stale_monitor_interval_seconds)
        except asyncio.TimeoutError:
            pass
