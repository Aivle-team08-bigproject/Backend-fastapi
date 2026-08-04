import asyncio
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.domains.pipeline.model import (
    DataRequest,
    PipelineRun,
    PipelineRunStatus,
    ProcessingStepCode,
    ProcessingStepStatus,
    SelectionStepCode,
    SelectionStepStatus,
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

    async def flush(self):
        if self.added and getattr(self.added[-1], "id", None) is None:
            self.added[-1].id = 101

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
    assert event.event_id == 101
    assert event.stage_run_id == 11


def test_sse_message_format():
    assert _sse_message("status", '{"run_status":"RUNNING"}') == (
        'event: status\ndata: {"run_status":"RUNNING"}\n\n'
    )


def test_sse_message_includes_database_event_id():
    assert _sse_message("status", '{"event_id":101}', 101) == (
        'id: 101\nevent: status\ndata: {"event_id":101}\n\n'
    )


def test_pipeline_status_event_serializes_optional_selection_step_contract():
    event = PipelineStatusEvent(
        run_id=7,
        celery_task_id="task-7",
        run_status=PipelineRunStatus.RUNNING,
        current_stage="DATA_SELECTION",
        stage_status=StageRunStatus.RUNNING,
        selection_step=SelectionStepCode.DERIVED_COLUMN_DESIGN,
        selection_step_status=SelectionStepStatus.RUNNING,
        attempt_no=2,
        progress_percent=45,
        message="파생 컬럼을 정의하고 있습니다.",
        occurred_at=datetime.now(timezone.utc),
    )

    payload = event.model_dump(mode="json")
    assert payload["selection_step"] == "DERIVED_COLUMN_DESIGN"
    assert payload["selection_step_status"] == "RUNNING"
    assert payload["attempt_no"] == 2


def test_pipeline_status_event_keeps_legacy_events_compatible():
    event = PipelineStatusEvent(
        run_id=7,
        celery_task_id="task-7",
        run_status=PipelineRunStatus.RUNNING,
        progress_percent=5,
        message="started",
        occurred_at=datetime.now(timezone.utc),
    )

    assert event.selection_step is None
    assert event.selection_step_status is None
    assert event.attempt_no is None


def test_pipeline_status_event_allows_attempt_transition_without_substep():
    event = PipelineStatusEvent(
        run_id=7,
        celery_task_id="task-7",
        run_status=PipelineRunStatus.RUNNING,
        current_stage="DATA_SELECTION",
        stage_status=StageRunStatus.RUNNING,
        attempt_no=2,
        progress_percent=56,
        message="새 데이터 선별 시도를 시작했습니다.",
        occurred_at=datetime.now(timezone.utc),
    )

    assert event.attempt_no == 2
    assert event.selection_step is None


def test_pipeline_status_event_serializes_processing_step_contract():
    event = PipelineStatusEvent(
        run_id=7,
        celery_task_id="task-7",
        run_status=PipelineRunStatus.RUNNING,
        current_stage="DATA_PROCESSING",
        stage_status=StageRunStatus.RUNNING,
        processing_step=ProcessingStepCode.DERIVED_COLUMN_ORDER,
        processing_step_status=ProcessingStepStatus.COMPLETED,
        attempt_no=2,
        progress_percent=87,
        message="파생 컬럼 생성 순서가 결정되었습니다.",
        occurred_at=datetime.now(timezone.utc),
    )

    payload = event.model_dump(mode="json")
    assert payload["processing_step"] == "DERIVED_COLUMN_ORDER"
    assert payload["processing_step_status"] == "COMPLETED"


def test_pipeline_status_event_rejects_partial_selection_step_fields():
    with pytest.raises(ValidationError, match="must be set together"):
        PipelineStatusEvent(
            run_id=7,
            celery_task_id="task-7",
            run_status=PipelineRunStatus.RUNNING,
            selection_step=SelectionStepCode.SOURCE_COLUMN_SELECTION,
            progress_percent=34,
            message="원본 컬럼을 선별하고 있습니다.",
            occurred_at=datetime.now(timezone.utc),
        )


def test_sse_rejects_missing_bearer_token(client):
    response = client.get("/api/v1/runs/7/events")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"
