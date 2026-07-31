import asyncio
from datetime import datetime, timezone

from app.domains.pipeline.model import (
    DataRequest,
    PipelineRun,
    PipelineRunStatus,
    StageRun,
    StageRunStatus,
)
from app.domains.pipeline.router import _sse_message
from app.worker.status_event import PipelineStatusEvent
from app.worker.status_recorder import persist_status_event


class FakeAsyncSession:
    def __init__(self, run, data_request, stage):
        self.run = run
        self.data_request = data_request
        self.stage = stage
        self.added = []
        self.commits = 0

    async def get(self, model, object_id):
        if model is PipelineRun:
            return self.run if self.run.id == object_id else None
        if model is DataRequest:
            return self.data_request if self.data_request.id == object_id else None
        return None

    async def scalar(self, statement):
        return self.stage

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1


def test_persist_running_pipeline_event():
    now = datetime.now(timezone.utc)
    run = PipelineRun(
        id=7,
        data_request_id=3,
        attempt_no=1,
        status="QUEUED",
        current_stage="REQUIREMENT_ANALYSIS",
        progress_percent=0,
        celery_task_id="task-7",
        created_at=now,
        updated_at=now,
    )
    data_request = DataRequest(
        id=3,
        client_id=1,
        request_no="REQ-TEST",
        title="test",
        raw_requirement="test",
        output_formats=[],
        delivery_channels=[],
        analysis_condition={},
        status="QUEUED",
        created_at=now,
        updated_at=now,
    )
    stage = StageRun(
        id=11,
        pipeline_run_id=7,
        stage_code="REQUIREMENT_ANALYSIS",
        attempt_no=1,
        status="PENDING",
        executor="CELERY",
        input_payload={},
        output_payload={},
        validation_result={},
        created_at=now,
    )
    db = FakeAsyncSession(run, data_request, stage)
    event = PipelineStatusEvent(
        run_id=7,
        celery_task_id="task-7",
        run_status=PipelineRunStatus.RUNNING,
        current_stage="REQUIREMENT_ANALYSIS",
        stage_status=StageRunStatus.RUNNING,
        progress_percent=5,
        message="started",
        occurred_at=now,
    )

    assert asyncio.run(persist_status_event(db, event)) is True
    assert run.status == "RUNNING"
    assert data_request.status == "RUNNING"
    assert stage.status == "RUNNING"
    assert stage.executor_reference == "task-7"
    assert db.commits == 1
    assert len(db.added) == 1


def test_sse_message_format():
    assert _sse_message("status", '{"run_status":"RUNNING"}') == (
        'event: status\ndata: {"run_status":"RUNNING"}\n\n'
    )
