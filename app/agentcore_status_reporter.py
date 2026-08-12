"""AgentCore Runtime의 PostgreSQL 진행 상태 writer.

Runtime은 Redis에 접속하지 않는다. 내부 단계 이벤트를 PipelineEvent와 StageRun snapshot에
직접 기록하고, FastAPI SSE가 해당 DB event를 읽어 브라우저에 전달한다.
"""

from __future__ import annotations

import asyncio
import logging
import time
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
# 상태 write가 이 시간을 넘기면 invocation이 끊기기 전에 조짐을 남긴다.
_SLOW_STATUS_WRITE_SECONDS = 5
# 한 stage가 만드는 이벤트는 20건 남짓이다. 넉넉히 잡되 무한 적재는 막는다.
_STATUS_QUEUE_MAXSIZE = 256
# 응답 반환 직전 flush 한도. 여기서 오래 기다리면 논블로킹 이점이 사라진다.
_STATUS_DRAIN_TIMEOUT_SECONDS = 10


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
        self._queue: asyncio.Queue | None = None
        self._consumer: asyncio.Task | None = None
        self._dropped = 0

    async def start(self) -> None:
        """상태 쓰기를 직렬 소비하는 background consumer를 띄운다.

        에이전트 스레드가 DB 왕복을 기다리지 않게 하되, 큐 하나를 순차 소비해서
        RUNNING -> COMPLETED 순서는 그대로 유지한다.
        """
        self._queue = asyncio.Queue(maxsize=_STATUS_QUEUE_MAXSIZE)
        self._consumer = asyncio.create_task(self._consume())

    async def _consume(self) -> None:
        assert self._queue is not None
        while True:
            operation = await self._queue.get()
            if operation is None:
                self._queue.task_done()
                return
            started = time.monotonic()
            try:
                await operation()
            except Exception:  # noqa: BLE001 - 상태 기록 장애가 산출물을 폐기하면 안 된다
                logger.exception(
                    "AgentCore status write failed after %.1fs: execution_id=%s stage=%s",
                    time.monotonic() - started,
                    self.execution_id,
                    self.stage_name.value,
                )
            else:
                elapsed = time.monotonic() - started
                if elapsed >= _SLOW_STATUS_WRITE_SECONDS:
                    logger.warning(
                        "AgentCore status write slow: %.1fs execution_id=%s stage=%s",
                        elapsed,
                        self.execution_id,
                        self.stage_name.value,
                    )
            finally:
                self._queue.task_done()

    async def drain(self) -> None:
        """남은 상태 쓰기를 flush한다. 응답 반환 직전에만 호출한다."""
        if self._queue is None or self._consumer is None:
            return
        try:
            await asyncio.wait_for(self._queue.join(), timeout=_STATUS_DRAIN_TIMEOUT_SECONDS)
        except (TimeoutError, asyncio.TimeoutError):
            logger.warning(
                "AgentCore status drain timed out: execution_id=%s stage=%s pending=%d",
                self.execution_id,
                self.stage_name.value,
                self._queue.qsize(),
            )
        if self._dropped:
            logger.warning(
                "AgentCore status events dropped: %d execution_id=%s",
                self._dropped,
                self.execution_id,
            )
        await self._queue.put(None)
        try:
            await asyncio.wait_for(self._consumer, timeout=_STATUS_DRAIN_TIMEOUT_SECONDS)
        except (TimeoutError, asyncio.TimeoutError):
            self._consumer.cancel()

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
        """에이전트 스레드에서 호출된다. **절대 블로킹하지 않는다.**

        이전 구현은 run_coroutine_threadsafe 후 future.result(timeout=30)로 DB 왕복을
        기다렸다. Runtime(ap-northeast-2)과 Neon(ap-southeast-1)이 교차 리전이라 이
        대기가 invocation 벽시계 시간을 밀어 올렸고, 진짜 424는 67~70초에 뭉쳐
        발생했다(2026-08-12 실측). 큐에 넣고 즉시 반환한다.
        """
        if self._queue is not None:
            try:
                self.event_loop.call_soon_threadsafe(self._queue.put_nowait, operation)
            except asyncio.QueueFull:
                # 상태 표시는 부가 경로다. 큐가 넘치면 산출물을 지키고 이벤트를 버린다.
                self._dropped += 1
            except RuntimeError:
                # loop가 이미 닫힌 종료 구간.
                self._dropped += 1
            return
        self._run_blocking(operation)

    def _run_blocking(self, operation: Callable[[], Awaitable[None]]) -> None:
        # AgentRuntimeClient는 모델 호출을 별도 thread에서 수행한다. RuntimeDatabase의
        # asyncpg pool은 Runtime event loop에 귀속되므로, callback thread에서 asyncio.run()
        # 으로 새 loop를 만들면 "Future attached to a different loop"가 난다. 반드시
        # Runtime loop에 coroutine을 예약하고 완료를 기다린다.
        started = time.monotonic()
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
            #
            # DB write가 막히면 DB 이벤트 자체가 안 남으므로 실패 사실을 DB에만 기록하면
            # 증거가 사라진다. 반드시 stderr로도 남겨 CloudWatch Runtime 로그에서 보이게 한다.
            logger.exception(
                "AgentCore status write failed after %.1fs: execution_id=%s stage=%s",
                time.monotonic() - started,
                self.execution_id,
                self.stage_name.value,
            )
        else:
            elapsed = time.monotonic() - started
            if elapsed >= _SLOW_STATUS_WRITE_SECONDS:
                logger.warning(
                    "AgentCore status write slow: %.1fs execution_id=%s stage=%s",
                    elapsed,
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
