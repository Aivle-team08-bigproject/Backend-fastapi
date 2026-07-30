"""Supervisor 단계 진행 로직 — DB 없이 단계 선택·payload 조립·검증 경로를 확인한다."""

import asyncio
from datetime import datetime, timezone

import pytest

from app.domains.pipeline.model import (
    DataRequest,
    PipelineRun,
    PipelineRunStatus,
    StageName,
    StageRun,
    StageRunStatus,
)
from app.domains.pipeline.supervisor import (
    HITL_GATE,
    STAGE_ORDER,
    StageDispatchError,
    build_stage_payload,
    next_pending_stage,
    rollback_target,
    run_stage,
)


NOW = datetime.now(timezone.utc)


def _run(**overrides) -> PipelineRun:
    defaults = dict(
        id=7,
        data_request_id=3,
        attempt_no=1,
        status=PipelineRunStatus.QUEUED.value,
        current_stage=StageName.REQUIREMENT_ANALYSIS.value,
        progress_percent=0,
        celery_task_id="task-7",
        rollback_to_stage=None,
        created_at=NOW,
        updated_at=NOW,
    )
    defaults.update(overrides)
    return PipelineRun(**defaults)


def _stage(stage_code: str, status: str, stage_id: int, attempt_no: int = 1, **overrides) -> StageRun:
    defaults = dict(
        id=stage_id,
        pipeline_run_id=7,
        stage_code=stage_code,
        attempt_no=attempt_no,
        status=status,
        executor="CELERY",
        input_payload={},
        output_payload={},
        validation_result={},
        created_at=NOW,
    )
    defaults.update(overrides)
    return StageRun(**defaults)


class FakeDb:
    """next_pending_stage / build_stage_payload가 쓰는 읽기 경로만 흉내낸다."""

    def __init__(self, run: PipelineRun, stages: list[StageRun], raw_requirement="서울 결제 데이터"):
        self.run = run
        self.stages = stages
        self.raw_requirement = raw_requirement

    async def get(self, model, object_id):
        if model is PipelineRun:
            return self.run if self.run.id == object_id else None
        if model is StageRun:
            return next((s for s in self.stages if s.id == object_id), None)
        if model is DataRequest:
            return None
        return None

    async def scalars(self, statement):
        # supervisor는 StageRun 목록만 scalars로 읽는다. _completed_outputs는 status를
        # COMPLETED로 바인딩해서 조회하므로 bind 파라미터로 두 질의를 구분한다.
        params = statement.compile().params
        if StageRunStatus.COMPLETED.value in params.values():
            rows = [s for s in self.stages if s.status == StageRunStatus.COMPLETED.value]
        else:
            rows = list(self.stages)
        return _Scalars(rows)

    async def scalar(self, statement):
        return self.raw_requirement


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


def test_stage_order_and_gates_cover_every_stage():
    assert [stage.value for stage in STAGE_ORDER] == [
        "REQUIREMENT_ANALYSIS",
        "DATA_SELECTION",
        "DATA_PROCESSING",
    ]
    assert set(HITL_GATE) == set(STAGE_ORDER)
    assert HITL_GATE[StageName.DATA_PROCESSING] == PipelineRunStatus.WAITING_FINAL_REVIEW


def test_next_pending_stage_follows_stage_order_not_row_order():
    stages = [
        _stage("DATA_PROCESSING", StageRunStatus.PENDING.value, 12),
        _stage("REQUIREMENT_ANALYSIS", StageRunStatus.PENDING.value, 10),
        _stage("DATA_SELECTION", StageRunStatus.PENDING.value, 11),
    ]
    db = FakeDb(_run(), stages)

    stage = asyncio.run(next_pending_stage(db, 7))

    assert stage.stage_code == "REQUIREMENT_ANALYSIS"


def test_next_pending_stage_skips_completed_stages():
    stages = [
        _stage("REQUIREMENT_ANALYSIS", StageRunStatus.COMPLETED.value, 10),
        _stage("DATA_SELECTION", StageRunStatus.PENDING.value, 11),
        _stage("DATA_PROCESSING", StageRunStatus.PENDING.value, 12),
    ]
    db = FakeDb(_run(), stages)

    stage = asyncio.run(next_pending_stage(db, 7))

    assert stage.stage_code == "DATA_SELECTION"


def test_next_pending_stage_refuses_while_a_stage_is_running():
    stages = [_stage("DATA_SELECTION", StageRunStatus.RUNNING.value, 11)]
    db = FakeDb(_run(), stages)

    with pytest.raises(StageDispatchError, match="already has a running stage"):
        asyncio.run(next_pending_stage(db, 7))


def test_next_pending_stage_honours_rollback_target():
    stages = [
        _stage("REQUIREMENT_ANALYSIS", StageRunStatus.ROLLED_BACK.value, 10),
        _stage("DATA_SELECTION", StageRunStatus.ROLLED_BACK.value, 11),
        _stage("REQUIREMENT_ANALYSIS", StageRunStatus.PENDING.value, 13, attempt_no=2),
        _stage("DATA_SELECTION", StageRunStatus.PENDING.value, 14, attempt_no=2),
    ]
    db = FakeDb(_run(rollback_to_stage="DATA_SELECTION"), stages)

    stage = asyncio.run(next_pending_stage(db, 7))

    assert stage.stage_code == "DATA_SELECTION"
    assert stage.attempt_no == 2


def test_next_pending_stage_raises_when_nothing_left():
    stages = [_stage("DATA_PROCESSING", StageRunStatus.COMPLETED.value, 12)]
    db = FakeDb(_run(), stages)

    with pytest.raises(StageDispatchError, match="no pending stage left"):
        asyncio.run(next_pending_stage(db, 7))


def test_requirement_analysis_payload_carries_raw_requirement():
    stage = _stage("REQUIREMENT_ANALYSIS", StageRunStatus.PENDING.value, 10)
    db = FakeDb(_run(), [stage])

    payload = asyncio.run(build_stage_payload(db, stage))

    assert payload == {"raw_requirement": "서울 결제 데이터"}


def test_data_selection_payload_requires_analysis_output():
    stage = _stage("DATA_SELECTION", StageRunStatus.PENDING.value, 11)
    db = FakeDb(_run(), [stage])

    with pytest.raises(StageDispatchError, match="requirement analysis output is missing"):
        asyncio.run(build_stage_payload(db, stage))


def test_data_selection_payload_reuses_completed_analysis_output():
    analysis = {"usage_purpose": "research", "categories": {"지역": "서울"}}
    stages = [
        _stage(
            "REQUIREMENT_ANALYSIS",
            StageRunStatus.COMPLETED.value,
            10,
            output_payload=analysis,
        ),
        _stage("DATA_SELECTION", StageRunStatus.PENDING.value, 11),
    ]
    db = FakeDb(_run(), stages)

    payload = asyncio.run(build_stage_payload(db, stages[1]))

    assert payload["analysis"] == analysis
    assert payload["raw_requirement"] == "서울 결제 데이터"
    assert payload["available_data"]


def test_data_processing_payload_ignores_stages_that_are_not_completed():
    """롤백된 이전 시도의 산출물이 새 시도 payload로 새지 않아야 한다."""
    stages = [
        _stage(
            "REQUIREMENT_ANALYSIS",
            StageRunStatus.ROLLED_BACK.value,
            10,
            output_payload={"usage_purpose": "stale"},
        ),
        _stage("DATA_SELECTION", StageRunStatus.ROLLED_BACK.value, 11, output_payload={"x": 1}),
        _stage("DATA_PROCESSING", StageRunStatus.PENDING.value, 12, attempt_no=2),
    ]
    db = FakeDb(_run(), stages)

    with pytest.raises(StageDispatchError, match="requirement analysis or data selection"):
        asyncio.run(build_stage_payload(db, stages[2]))


class RejectingAgentClient:
    """필수 키가 빠진 산출물을 돌려주는 에이전트 — 검증 실패 경로 확인용."""

    async def run(self, agent_name: str, model_name: str, payload: dict) -> dict:
        return {"_agent_error": "model unavailable"}


def test_failed_validation_marks_run_failed_and_keeps_failure_code():
    stage = _stage("REQUIREMENT_ANALYSIS", StageRunStatus.PENDING.value, 10)
    db = FakeDb(_run(), [stage])

    outcome = asyncio.run(run_stage(db, stage, agent_client=RejectingAgentClient()))

    assert outcome["passed"] is False
    assert outcome["run_status"] == PipelineRunStatus.FAILED
    assert outcome["validation"]["failure_code"] == "REQUIRED_KEY_MISSING"
    assert outcome["artifact"] is None


class ApprovingAgentClient:
    async def run(self, agent_name: str, model_name: str, payload: dict) -> dict:
        return {
            "usage_purpose": "research",
            "requested_data_sentence": "결제 데이터 분석",
            "categories": {"지역": "서울"},
            "delivery_channel": "API",
            "output_formats": ["csv"],
        }


def test_passing_stage_stops_at_its_hitl_gate():
    stage = _stage("REQUIREMENT_ANALYSIS", StageRunStatus.PENDING.value, 10)
    db = FakeDb(_run(), [stage])

    outcome = asyncio.run(run_stage(db, stage, agent_client=ApprovingAgentClient()))

    assert outcome["passed"] is True
    assert outcome["run_status"] == PipelineRunStatus.WAITING_REQUIREMENT_REVIEW
    assert outcome["progress_percent"] == 33
    assert outcome["error_message"] is None


def test_rollback_target_maps_failure_codes_to_stages():
    assert rollback_target("INSUFFICIENT_DATA") == StageName.DATA_SELECTION
    assert rollback_target("PROCESSING_RULE_INVALID") == StageName.DATA_PROCESSING
    assert rollback_target("HUMAN_REJECTED") == StageName.REQUIREMENT_ANALYSIS
    # 알 수 없는 값과 None은 처음부터 다시 돈다.
    assert rollback_target("NOT_A_REAL_CODE") == StageName.REQUIREMENT_ANALYSIS
    assert rollback_target(None) == StageName.REQUIREMENT_ANALYSIS
