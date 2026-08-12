"""AgentCore Runtime의 PostgreSQL 진행 상태 writer.

Runtime은 Redis에 접속하지 않는다. 내부 단계 이벤트를 PipelineEvent와 StageRun snapshot에
직접 기록하고, FastAPI SSE가 해당 DB event를 읽어 브라우저에 전달한다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import TimeoutError as FutureTimeoutError

from sqlalchemy import select

from app.domains.employees import model as employee_model  # noqa: F401
from app.domains.pipeline.analysis_steps import analysis_step_message, analysis_step_progress
from app.domains.pipeline.model import (
    AnalysisStepCode,
    AnalysisStepStatus,
    PipelineRun,
    PipelineRunStatus,
    ProcessingStepCode,
    ProcessingStepStatus,
    SelectionStepCode,
    SelectionStepStatus,
    StageName,
    StageRun,
    StageRunStatus,
)
from app.domains.pipeline.processing_steps import processing_step_message, processing_step_progress
from app.domains.pipeline.selection_steps import selection_step_message, selection_step_progress
from app.worker.status_recorder import record_agent_log, record_status


_STAGE_BY_AGENT = {
    "requirement-analysis-agent": StageName.REQUIREMENT_ANALYSIS,
    "data-selection-agent": StageName.DATA_SELECTION,
    "data-processing-agent": StageName.DATA_PROCESSING,
}
logger = logging.getLogger("gunicorn.error")
_STATUS_WRITE_TIMEOUT_SECONDS = 30


class AgentCoreStatusReporter:
    """동기 agent callback을 Runtime 전용 DB 세션으로 안전하게 영속화한다."""

    def __init__(
        self,
        *,
        execution_id: str,
        agent_name: str,
        session_factory,
        event_loop: asyncio.AbstractEventLoop | None = None,
    ):
        self.execution_id = execution_id
        self.stage_name = _STAGE_BY_AGENT[agent_name]
        self.session_factory = session_factory
        self.event_loop = event_loop

    async def _context(self, db) -> tuple[PipelineRun, StageRun] | None:
        run = await db.scalar(
            select(PipelineRun).where(PipelineRun.execution_id == self.execution_id)
        )
        if run is None:
            return None
        stage = await db.scalar(
            select(StageRun)
            .where(
                StageRun.pipeline_run_id == run.id,
                StageRun.stage_code == self.stage_name.value,
            )
            .order_by(StageRun.attempt_no.desc(), StageRun.id.desc())
            .limit(1)
        )
        return (run, stage) if stage is not None else None

    def _run(self, operation: Callable[[], Awaitable[None]]) -> None:
        # AgentRuntimeClient는 모델 호출을 별도 thread에서 수행한다. RuntimeDatabase의
        # asyncpg pool은 Runtime event loop에 귀속되므로, callback thread에서 asyncio.run()
        # 으로 새 loop를 만들면 "Future attached to a different loop"가 난다. 반드시
        # Runtime loop에 coroutine을 예약하고 완료를 기다린다.
        try:
            if self.event_loop is None:
                # 단위 테스트 등 Runtime loop 밖에서만 쓰는 fallback이다.
                asyncio.run(operation())
                return
            future = asyncio.run_coroutine_threadsafe(operation(), self.event_loop)
            try:
                future.result(timeout=_STATUS_WRITE_TIMEOUT_SECONDS)
            except FutureTimeoutError:
                future.cancel()
                raise TimeoutError("AgentCore status write timed out") from None
        except Exception:  # noqa: BLE001 - 상태 기록 장애가 에이전트 산출물을 폐기하면 안 된다
            # PostgreSQL 이벤트는 실시간 표시용 부가 경로다. 중간 상태 저장이 일시적으로
            # 실패해도 최종 산출물은 호출 FastAPI가 기록할 수 있도록 invocation을 계속한다.
            logger.exception(
                "AgentCore status write failed: execution_id=%s stage=%s",
                self.execution_id,
                self.stage_name.value,
            )

    async def _record_log(self, level: str, message: str, detail: dict | None) -> None:
        async with self.session_factory() as db:
            context = await self._context(db)
            if context is None:
                return
            run, stage = context
            await record_agent_log(
                db,
                run_id=run.id,
                execution_id=self.execution_id,
                message=message,
                level=level,
                current_stage=self.stage_name.value,
                stage_run_id=stage.id,
                detail=detail,
                publish=False,
            )

    def agent_log_callback(self, level: str, message: str, detail: dict | None) -> None:
        self._run(lambda: self._record_log(level, message, detail))

    async def _record_analysis_step(self, code: str, status: str, metadata: dict | None) -> None:
        step = AnalysisStepCode(code)
        step_status = AnalysisStepStatus(status)
        await self._record_step(
            analysis_step=step,
            analysis_step_status=step_status,
            progress_percent=analysis_step_progress(step, step_status),
            message=analysis_step_message(step, step_status),
            metadata=metadata,
        )

    def requirement_analysis_step_callback(
        self, code: str, status: str, metadata: dict | None
    ) -> None:
        self._run(lambda: self._record_analysis_step(code, status, metadata))

    async def _record_selection_step(self, code: str, status: str, metadata: dict | None) -> None:
        step = SelectionStepCode(code)
        step_status = SelectionStepStatus(status)
        await self._record_step(
            selection_step=step,
            selection_step_status=step_status,
            progress_percent=selection_step_progress(step, step_status),
            message=selection_step_message(step, step_status),
            metadata=metadata,
        )

    def selection_step_callback(self, code: str, status: str, metadata: dict | None) -> None:
        self._run(lambda: self._record_selection_step(code, status, metadata))

    async def _record_processing_step(self, code: str, status: str, metadata: dict | None) -> None:
        step = ProcessingStepCode(code)
        step_status = ProcessingStepStatus(status)
        await self._record_step(
            processing_step=step,
            processing_step_status=step_status,
            progress_percent=processing_step_progress(step, step_status),
            message=processing_step_message(step, step_status),
            metadata=metadata,
        )

    def processing_step_callback(self, code: str, status: str, metadata: dict | None) -> None:
        self._run(lambda: self._record_processing_step(code, status, metadata))

    async def _record_step(self, *, progress_percent: int, message: str, metadata: dict | None, **step_fields) -> None:
        async with self.session_factory() as db:
            context = await self._context(db)
            if context is None:
                return
            run, stage = context
            await record_status(
                db,
                run_id=run.id,
                execution_id=self.execution_id,
                run_status=PipelineRunStatus.RUNNING,
                current_stage=self.stage_name.value,
                stage_status=StageRunStatus.RUNNING,
                attempt_no=stage.attempt_no,
                progress_percent=progress_percent,
                message=message,
                step_metadata=metadata,
                publish=False,
                **step_fields,
            )
