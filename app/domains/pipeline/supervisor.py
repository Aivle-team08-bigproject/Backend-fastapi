"""감시 Supervisor — 단계 진행을 결정하고 각 단계 에이전트 실행을 감독한다.

책임 분리:
- 이 모듈은 "다음에 어느 단계를 돌릴지" 결정하고, 그 단계 에이전트를 호출해 산출물을
  검증한다. 순수 판단·실행 계층이라 DB 쓰기는 하지 않는다.
- 상태 쓰기는 호출자인 Worker(app/worker/tasks.py)가 app/worker/status_recorder.py를 통해
  한다. Worker가 DB에 쓴 뒤 Redis로 발행하고, FastAPI SSE가 그걸 프론트 화면으로 흘린다.

단계 진행은 각 단계가 끝날 때마다 사람 승인(HITL)에서 멈춘다:
    단계 실행 -> 산출물 검증 -> WAITING_*_REVIEW -> 승인 -> 다음 단계
"""

import base64

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pipeline.agent_client import AgentClient, AgentRuntimeClient
from app.domains.pipeline.model import (
    FailureCode,
    PipelineRun,
    PipelineRunStatus,
    ReviewDecision,
    StageName,
    StageRun,
    StageRunStatus,
)
from app.domains.pipeline.validation import validate_stage_output
from app.domains.pipeline.plan_integrity import verify_selection_plan

# Supervisor가 순서대로 진행시키는 단계. 데이터 조회는 DATA_PROCESSING 안에서
# query 레이어가 담당한다.
STAGE_ORDER: tuple[StageName, ...] = (
    StageName.REQUIREMENT_ANALYSIS,
    StageName.DATA_SELECTION,
    StageName.DATA_PROCESSING,
)

# 단계별 진행률. 기존 SSE 계약(33/66/100)을 그대로 유지한다.
STAGE_PROGRESS: dict[StageName, int] = {
    StageName.REQUIREMENT_ANALYSIS: 33,
    StageName.DATA_SELECTION: 66,
    StageName.DATA_PROCESSING: 100,
}

# 단계가 끝난 뒤 어느 승인 대기 상태로 멈출지.
HITL_GATE: dict[StageName, PipelineRunStatus] = {
    StageName.REQUIREMENT_ANALYSIS: PipelineRunStatus.WAITING_REQUIREMENT_REVIEW,
    StageName.DATA_SELECTION: PipelineRunStatus.WAITING_SAMPLE_REVIEW,
    StageName.DATA_PROCESSING: PipelineRunStatus.WAITING_FINAL_REVIEW,
}

AGENT_FOR_STAGE: dict[StageName, str] = {
    StageName.REQUIREMENT_ANALYSIS: "requirement-analysis-agent",
    StageName.DATA_SELECTION: "data-selection-agent",
    StageName.DATA_PROCESSING: "data-processing-agent",
}

AVAILABLE_DATA = ["merchant", "member_pseudonymized", "transaction_pseudonymized"]


class StageDispatchError(RuntimeError):
    """진행시킬 단계를 정할 수 없을 때."""


async def next_pending_stage(db: AsyncSession, run_id: int) -> StageRun:
    """다음에 실행할 PENDING 단계를 STAGE_ORDER 순서로 고른다.

    stage_runs 행은 요청 생성 시점에 미리 만들어져 있으므로(service.create_data_request)
    새로 만들지 않고 아직 안 돌린 행을 claim한다. 롤백된 재시도 행도 PENDING이라 같은
    경로로 잡힌다.
    """
    run = await db.get(PipelineRun, run_id)
    if run is None:
        raise StageDispatchError(f"pipeline run not found: {run_id}")

    stages = list(
        (
            await db.scalars(
                select(StageRun)
                .where(StageRun.pipeline_run_id == run_id)
                .order_by(StageRun.attempt_no, StageRun.created_at, StageRun.id)
            )
        ).all()
    )
    running = [stage for stage in stages if stage.status == StageRunStatus.RUNNING.value]
    if running:
        raise StageDispatchError(
            f"run {run_id} already has a running stage: {running[0].stage_code}"
        )

    # 반려로 되돌아갈 단계가 지정돼 있으면 그 단계부터 다시 시작한다.
    if run.rollback_to_stage:
        target = StageName(run.rollback_to_stage)
        for stage in stages:
            if stage.stage_code == target.value and stage.status == StageRunStatus.PENDING.value:
                return stage
        raise StageDispatchError(f"no pending stage for rollback target {target.value}")

    by_code = {}
    for stage in stages:
        if stage.status == StageRunStatus.PENDING.value:
            by_code.setdefault(stage.stage_code, stage)
    for stage_name in STAGE_ORDER:
        if stage_name.value in by_code:
            return by_code[stage_name.value]
    raise StageDispatchError(f"run {run_id} has no pending stage left")


async def build_stage_payload(db: AsyncSession, stage: StageRun) -> dict:
    """단계 입력 payload를 앞선 단계들의 산출물로 조립한다."""
    run = await db.get(PipelineRun, stage.pipeline_run_id)
    if run is None:
        raise StageDispatchError(f"pipeline run not found: {stage.pipeline_run_id}")
    raw_requirement = await _raw_requirement(db, run)
    stage_name = StageName(stage.stage_code)

    if stage_name == StageName.REQUIREMENT_ANALYSIS:
        return {"raw_requirement": raw_requirement}

    completed = await _completed_outputs(db, run.id)

    if stage_name == StageName.DATA_SELECTION:
        analysis = completed.get(StageName.REQUIREMENT_ANALYSIS.value)
        if analysis is None:
            raise StageDispatchError("requirement analysis output is missing")
        payload = {
            "raw_requirement": raw_requirement,
            "analysis": analysis,
            "available_data": AVAILABLE_DATA,
        }
        if feedback := await _retry_feedback(db, stage):
            payload["hitl_feedback"] = feedback
        return payload

    if stage_name == StageName.DATA_PROCESSING:
        analysis = completed.get(StageName.REQUIREMENT_ANALYSIS.value)
        approval = (stage.input_payload or {}).get("approved_selection")
        if analysis is None:
            raise StageDispatchError("requirement analysis output is missing")
        if not isinstance(approval, dict):
            raise StageDispatchError("approved selection plan is missing")
        selection = approval.get("plan")
        expected_sha256 = approval.get("sha256")
        if not isinstance(selection, dict) or not isinstance(expected_sha256, str):
            raise StageDispatchError("approved selection plan is invalid")
        try:
            verify_selection_plan(selection, expected_sha256)
        except ValueError as exc:
            raise StageDispatchError(str(exc)) from exc
        payload = {
            "raw_requirement": raw_requirement,
            "analysis": analysis,
            "selection": selection,
            "approval_audit": {
                "stage_run_id": approval.get("stage_run_id"),
                "sha256": expected_sha256,
                "approved_at": approval.get("approved_at"),
                "reviewer_id": approval.get("reviewer_id"),
                "reviewer_name": approval.get("reviewer_name"),
            },
        }
        if feedback := await _retry_feedback(db, stage):
            payload["hitl_feedback"] = feedback
        return payload

    raise StageDispatchError(f"unsupported stage: {stage.stage_code}")


async def _retry_feedback(db: AsyncSession, stage: StageRun) -> str | None:
    """직전 산출물에 대한 HITL 수정 의견을 재시도 Agent에 전달한다."""
    if stage.retry_of_id is None:
        return None
    from app.domains.pipeline.model import Review

    return await db.scalar(
        select(Review.feedback)
        .where(
            Review.stage_run_id == stage.retry_of_id,
            Review.decision == ReviewDecision.CHANGES_REQUESTED.value,
        )
        .order_by(Review.created_at.desc(), Review.id.desc())
        .limit(1)
    )


async def _raw_requirement(db: AsyncSession, run: PipelineRun) -> str:
    from app.domains.pipeline.model import DataRequest

    raw_requirement = await db.scalar(
        select(DataRequest.raw_requirement).where(DataRequest.id == run.data_request_id)
    )
    if raw_requirement is None:
        raise StageDispatchError(f"data request not found for run {run.id}")
    return raw_requirement


async def _completed_outputs(db: AsyncSession, run_id: int) -> dict[str, dict]:
    """단계코드 -> 가장 최근 완료 산출물."""
    stages = list(
        (
            await db.scalars(
                select(StageRun)
                .where(
                    StageRun.pipeline_run_id == run_id,
                    StageRun.status == StageRunStatus.COMPLETED.value,
                )
                .order_by(StageRun.attempt_no, StageRun.created_at, StageRun.id)
            )
        ).all()
    )
    return {stage.stage_code: (stage.output_payload or {}) for stage in stages}


async def run_stage(
    db: AsyncSession,
    stage: StageRun,
    agent_client: AgentClient | None = None,
    selection_step_callback=None,
    processing_step_callback=None,
) -> dict:
    """단계 에이전트를 실행하고 산출물을 검증한다.

    DB에 쓰지 않고 판단 결과만 돌려준다 — 호출자(Celery task)가 status_recorder로 DB에
    쓰고 화면 갱신까지 처리한다.

    반환: {stage_name, passed, output, validation, run_status, progress_percent, artifact}
    """
    client = agent_client or AgentRuntimeClient()
    if selection_step_callback is not None and hasattr(
        client, "selection_step_callback"
    ):
        client.selection_step_callback = selection_step_callback
    if processing_step_callback is not None and hasattr(
        client, "processing_step_callback"
    ):
        client.processing_step_callback = processing_step_callback
    stage_name = StageName(stage.stage_code)
    payload = await build_stage_payload(db, stage)

    try:
        output = await client.run(
            AGENT_FOR_STAGE[stage_name],
            stage.model_name or "",
            payload,
        )
        validation = validate_stage_output(stage_name, output)
    except Exception as exc:  # noqa: BLE001 - 실패도 이벤트로 남겨야 한다
        output = {"_worker_error": str(exc)}
        validation = {"passed": False, "errors": [str(exc)], "failure_code": None}

    if not validation["passed"]:
        return {
            "stage_name": stage_name,
            "passed": False,
            "output": output,
            "validation": validation,
            "run_status": PipelineRunStatus.FAILED,
            "progress_percent": 0,
            "artifact": None,
            "error_message": "; ".join(validation["errors"]) or "stage validation failed",
        }

    artifact = None
    if stage_name == StageName.DATA_PROCESSING:
        artifact = _write_csv_artifact(stage.pipeline_run_id, output)

    return {
        "stage_name": stage_name,
        "passed": True,
        "output": output,
        "validation": validation,
        "run_status": HITL_GATE[stage_name],
        "progress_percent": STAGE_PROGRESS[stage_name],
        "artifact": artifact,
        "error_message": None,
    }


def _write_csv_artifact(run_id: int, output: dict) -> dict | None:
    """가공 산출물의 CSV를 파일로 저장하고 메타데이터를 돌려준다."""
    csv_artifact = (output or {}).get("csv_artifact")
    if not csv_artifact or not csv_artifact.get("content_base64"):
        return None
    from app.worker.file_storage import write_result

    return write_result(
        run_id,
        base64.b64decode(csv_artifact["content_base64"], validate=True),
    )


def rollback_target(failure_code: str | None) -> StageName:
    """실패 사유별로 어느 단계까지 되돌릴지.

    반려(HITL)와 검증 실패 모두 이 표를 쓴다. 매핑에 없는 값은 처음부터 다시 돈다.
    """
    policy: dict[FailureCode, StageName] = {
        FailureCode.SCHEMA_INVALID: StageName.REQUIREMENT_ANALYSIS,
        FailureCode.REQUIRED_KEY_MISSING: StageName.REQUIREMENT_ANALYSIS,
        FailureCode.LOGICAL_CONTRADICTION: StageName.REQUIREMENT_ANALYSIS,
        FailureCode.MISINTERPRETED_REQUIREMENT: StageName.REQUIREMENT_ANALYSIS,
        FailureCode.HUMAN_REJECTED: StageName.REQUIREMENT_ANALYSIS,
        FailureCode.INSUFFICIENT_DATA: StageName.DATA_SELECTION,
        FailureCode.LOW_SIMILARITY_MATCH: StageName.DATA_SELECTION,
        FailureCode.DUPLICATED_DATA: StageName.DATA_SELECTION,
        FailureCode.OUTLIER_DETECTED: StageName.DATA_SELECTION,
        FailureCode.FORMAT_INVALID: StageName.DATA_PROCESSING,
        FailureCode.PROCESSING_RULE_INVALID: StageName.DATA_PROCESSING,
        FailureCode.PRIVACY_THRESHOLD_NOT_MET: StageName.DATA_SELECTION,
    }
    if not failure_code:
        return StageName.REQUIREMENT_ANALYSIS
    try:
        return policy[FailureCode(failure_code)]
    except (ValueError, KeyError):
        return StageName.REQUIREMENT_ANALYSIS
