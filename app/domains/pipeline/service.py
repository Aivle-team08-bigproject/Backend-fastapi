from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import not_found
from app.common.time_utils import utcnow
from app.domains.pipeline.model import (
    Client,
    DataRequest,
    DataRequestStatus,
    EventType,
    PipelineEvent,
    PipelineRun,
    PipelineRunStatus,
    StageRun,
    StageRunStatus,
)
from app.domains.pipeline.schema import (
    CreateDataRequestRequest,
    CreateDataRequestResponse,
    PipelineRunResponse,
    RunEventResponse,
    RunStageResponse,
)


def _make_request_no() -> str:
    return f"REQ-{utcnow():%Y%m%d}-{uuid4().hex[:6].upper()}"


async def create_data_request(
    db: AsyncSession, payload: CreateDataRequestRequest
) -> CreateDataRequestResponse:
    client = await db.scalar(select(Client).where(Client.company_name == payload.requester_name))
    if client is None:
        client = Client(company_name=payload.requester_name)
        db.add(client)
        await db.flush()

    title = payload.title or payload.raw_requirement.strip().splitlines()[0][:200]
    data_request = DataRequest(
        request_no=_make_request_no(),
        client_id=client.id,
        requester_name=payload.requester_name,
        title=title,
        raw_requirement=payload.raw_requirement.strip(),
        output_formats=[],
        delivery_channels=[],
        analysis_condition={},
        status=DataRequestStatus.QUEUED,
        current_stage="REQUIREMENT_ANALYSIS",
    )
    db.add(data_request)
    await db.flush()

    run = PipelineRun(
        data_request_id=data_request.id,
        attempt_no=1,
        status=PipelineRunStatus.QUEUED,
        current_stage="REQUIREMENT_ANALYSIS",
        progress_percent=0,
    )
    db.add(run)
    await db.flush()

    stage = StageRun(
        pipeline_run_id=run.id,
        stage_code="REQUIREMENT_ANALYSIS",
        attempt_no=1,
        status=StageRunStatus.PENDING,
        input_payload={
            "request_no": data_request.request_no,
            "raw_requirement": data_request.raw_requirement,
        },
    )
    db.add(stage)
    await db.flush()
    db.add(
        PipelineEvent(
            pipeline_run_id=run.id,
            stage_run_id=stage.id,
            event_type=EventType.PROGRESS,
            message="요청이 등록되어 요구사항 분석 실행을 기다리고 있습니다.",
            payload={"stage": "REQUIREMENT_ANALYSIS", "progress_percent": 0},
        )
    )
    await db.commit()

    return CreateDataRequestResponse(
        request_no=data_request.request_no,
        run_id=run.id,
        request_status=data_request.status,
        run_status=run.status,
        current_stage=run.current_stage or "REQUIREMENT_ANALYSIS",
        created_at=run.created_at,
    )


async def get_pipeline_run(db: AsyncSession, run_id: int) -> PipelineRunResponse:
    result = await db.execute(
        select(PipelineRun, DataRequest)
        .join(DataRequest, DataRequest.id == PipelineRun.data_request_id)
        .where(PipelineRun.id == run_id)
    )
    row = result.one_or_none()
    if row is None:
        raise not_found("PIPELINE_RUN_NOT_FOUND", "파이프라인 실행을 찾을 수 없습니다.")
    run, data_request = row

    stages = list(
        (
            await db.scalars(
                select(StageRun)
                .where(StageRun.pipeline_run_id == run.id)
                .order_by(StageRun.created_at, StageRun.id)
            )
        ).all()
    )
    events = list(
        (
            await db.scalars(
                select(PipelineEvent)
                .where(PipelineEvent.pipeline_run_id == run.id)
                .order_by(PipelineEvent.id)
            )
        ).all()
    )
    return PipelineRunResponse(
        run_id=run.id,
        request_no=data_request.request_no,
        request_title=data_request.title,
        raw_requirement=data_request.raw_requirement,
        request_status=data_request.status,
        run_status=run.status,
        current_stage=run.current_stage,
        progress_percent=run.progress_percent,
        created_at=run.created_at,
        updated_at=run.updated_at,
        stages=[
            RunStageResponse(
                stage_code=stage.stage_code,
                status=stage.status,
                created_at=stage.created_at,
            )
            for stage in stages
        ],
        events=[
            RunEventResponse(
                id=event.id,
                event_type=event.event_type,
                severity=event.severity,
                message=event.message,
                payload=event.payload,
                occurred_at=event.occurred_at,
            )
            for event in events
        ],
    )
