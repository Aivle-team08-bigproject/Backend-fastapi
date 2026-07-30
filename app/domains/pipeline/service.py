from uuid import uuid4

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import DomainException, not_found
from app.common.time_utils import utcnow
from app.domains.pipeline.model import (
    Artifact,
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
from app.domains.dashboard.model import TaskViewSnapshot
from app.domains.pipeline.schema import (
    CreateDataRequestRequest,
    CreateDataRequestResponse,
    PipelineRunResponse,
    RunEventResponse,
    RunStageResponse,
)
from app.worker.tasks import process_pipeline_run


def _make_request_no() -> str:
    return f"REQ-{utcnow():%Y%m%d}-{uuid4().hex[:6].upper()}"


HARDCODED_STAGES = (
    "REQUIREMENT_ANALYSIS",
    "DATA_SELECTION",
    "DATA_PROCESSING",
)

HARDCODED_VIEW_PAYLOADS = {
    "register": {
        "placeholder": "예: 강남구 외식업 소비 트렌드를 2024년 1~6월 기간으로 분석하고 싶습니다.",
    },
    "analysis": {
        "timelineItems": [
            {"title": "요건 파싱", "time": "14:23:05", "description": "요구사항 구조화 완료", "state": "done"},
            {"title": "데이터셋 매칭", "time": "14:23:12", "description": "내부 데이터 매핑 완료", "state": "done"},
            {"title": "실현가능성 검증", "time": "14:23:45", "description": "가용 데이터 검증 완료", "state": "done"},
        ],
        "logLines": [
            {"time": "14:23:05", "agent": "HARDCODED", "agentColor": "#008485", "message": "요구사항 분석이 완료되었습니다."},
        ],
    },
    "selection": {
        "timelineItems": [
            {"title": "데이터 소스 탐색", "time": "15:01:22", "description": "관련 데이터 소스 식별 완료", "state": "done"},
            {"title": "샘플 데이터 추출", "time": "15:08:47", "description": "대표 샘플 추출 완료", "state": "done"},
            {"title": "품질 스코어 산정", "time": "15:12:30", "description": "품질 검증 완료", "state": "done"},
        ],
        "logLines": [
            {"time": "15:12:30", "agent": "HARDCODED", "agentColor": "#008485", "message": "데이터 선별이 완료되었습니다."},
        ],
    },
    "processing": {
        "timelineItems": [
            {"title": "결측값 보정", "time": "16:05:10", "description": "결측값 보정 완료", "state": "done"},
            {"title": "형식 변환", "time": "16:18:33", "description": "CSV 포맷 변환 완료", "state": "done"},
            {"title": "익명화 처리", "time": "16:25:00", "description": "PII 마스킹 처리 완료", "state": "done"},
        ],
        "logLines": [
            {"time": "16:25:00", "agent": "HARDCODED", "agentColor": "#008485", "message": "데이터 가공이 완료되었습니다."},
        ],
    },
    "review": {
        "usagePurpose": "요구사항 기반 소비 트렌드 분석",
        "dataDescription": "익명화된 카드 결제 및 가맹점 데이터",
        "columns": [],
        "estimatedCount": "약 800건",
        "deliveryMedium": "웹 다운로드",
        "outputFormat": "CSV, XLSX",
        "feedbackPlaceholder": "검토 의견을 입력해주세요.",
    },
    "sample-feedback": {
        "sampleRows": [],
        "columnInfo": [],
        "feedbackPlaceholder": "샘플 데이터에 대한 의견을 입력해주세요.",
    },
    "final-feedback": {
        "outputRows": [],
        "reportTitle": "소비 트렌드 분석 결과",
        "reportMeta": "하드코딩 데모 결과",
        "insightSummary": ["요청 조건에 맞는 분석 결과가 생성되었습니다."],
        "chartBars": [],
        "infoRows": [],
        "feedbackPlaceholder": "최종 산출물에 대한 의견을 입력해주세요.",
    },
    "complete": {
        "milestones": [
            {"title": "요구사항 분석", "time": "14:23", "description": "요구사항 분석 완료"},
            {"title": "데이터 선별", "time": "15:12", "description": "데이터 선별 완료"},
            {"title": "데이터 가공", "time": "16:25", "description": "데이터 가공 완료"},
        ],
        "files": [
            {"name": "Final_Dataset.csv", "size": "42.8MB", "kind": "csv"},
            {"name": "Final_Dataset.xlsx", "size": "51.2MB", "kind": "xlsx"},
        ],
        "endpointUrl": "https://api.example.invalid/v1/datasets/demo",
        "apiKeyMasked": "hk_live_****************",
        "recipientEmail": "demo@example.invalid",
        "emailSubject": "데이터 가공 결과 전달",
    },
}


async def create_data_request(
    db: AsyncSession, payload: CreateDataRequestRequest
) -> CreateDataRequestResponse:
    now = utcnow()
    client = await db.scalar(select(Client).where(Client.company_name == payload.requester_name))
    if client is None:
        client = Client(company_name=payload.requester_name, created_at=now, updated_at=now)
        db.add(client)
        await db.flush()

    title = payload.title or payload.raw_requirement.strip().splitlines()[0][:200]
    celery_task_id = str(uuid4())
    data_request = DataRequest(
        request_no=_make_request_no(),
        client_id=client.id,
        requester_name=payload.requester_name,
        title=title,
        raw_requirement=payload.raw_requirement.strip(),
        output_formats=["CSV", "XLSX"],
        delivery_channels=["FILE_DOWNLOAD"],
        analysis_condition={"async_pipeline": True},
        status=DataRequestStatus.QUEUED,
        created_at=now,
        updated_at=now,
    )
    db.add(data_request)
    await db.flush()

    run = PipelineRun(
        data_request_id=data_request.id,
        attempt_no=1,
        status=PipelineRunStatus.QUEUED,
        current_stage=HARDCODED_STAGES[0],
        progress_percent=0,
        celery_task_id=celery_task_id,
        created_at=now,
        updated_at=now,
    )
    db.add(run)
    await db.flush()

    for stage_code in HARDCODED_STAGES:
        stage = StageRun(
            pipeline_run_id=run.id,
            stage_code=stage_code,
            attempt_no=1,
            status=StageRunStatus.PENDING,
            executor="CELERY",
            input_payload={"request_no": data_request.request_no},
            output_payload={},
            created_at=now,
        )
        db.add(stage)

    for view_code, view_payload in HARDCODED_VIEW_PAYLOADS.items():
        db.add(
            TaskViewSnapshot(
                data_request_id=data_request.id,
                view_code=view_code,
                payload=view_payload,
                created_at=now,
                updated_at=now,
            )
        )
    await db.commit()

    try:
        process_pipeline_run.apply_async(args=[run.id], task_id=celery_task_id)
    except Exception as exc:
        data_request.status = DataRequestStatus.FAILED.value
        run.status = PipelineRunStatus.FAILED.value
        run.current_stage = "DISPATCH"
        run.completed_at = utcnow()
        run.updated_at = run.completed_at
        db.add(
            PipelineEvent(
                pipeline_run_id=run.id,
                event_type=EventType.FAILED.value,
                severity="ERROR",
                message="Celery 작업 발행에 실패했습니다.",
                payload={"error_message": str(exc)},
                occurred_at=run.completed_at,
            )
        )
        await db.commit()
        raise DomainException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "PIPELINE_DISPATCH_FAILED",
            "작업 큐에 요청을 발행하지 못했습니다.",
        ) from exc

    return CreateDataRequestResponse(
        request_no=data_request.request_no,
        run_id=run.id,
        request_status=DataRequestStatus.QUEUED,
        run_status=PipelineRunStatus.QUEUED,
        current_stage=run.current_stage or HARDCODED_STAGES[0],
        celery_task_id=celery_task_id,
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
        celery_task_id=run.celery_task_id,
        created_at=run.created_at,
        updated_at=run.updated_at,
        stages=[
            RunStageResponse(
                stage_code=stage.stage_code,
                status=stage.status,
                executor=stage.executor,
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


async def dispatch_uploaded_csv(
    db: AsyncSession,
    run_id: int,
    upload_metadata: dict,
) -> str:
    run = await db.get(PipelineRun, run_id)
    if run is None:
        raise not_found("PIPELINE_RUN_NOT_FOUND", "파이프라인 실행을 찾을 수 없습니다.")
    if run.status in {
        PipelineRunStatus.RUNNING.value,
        PipelineRunStatus.COMPLETED.value,
        PipelineRunStatus.CANCELLED.value,
    }:
        raise DomainException(
            status.HTTP_409_CONFLICT,
            "PIPELINE_RUN_NOT_UPLOADABLE",
            "현재 실행 상태에서는 CSV를 업로드할 수 없습니다.",
        )

    celery_task_id = str(uuid4())
    now = utcnow()
    run.celery_task_id = celery_task_id
    run.status = PipelineRunStatus.QUEUED.value
    run.current_stage = HARDCODED_STAGES[0]
    run.progress_percent = 0
    run.started_at = None
    run.completed_at = None
    run.updated_at = now

    stages = list(
        (await db.scalars(select(StageRun).where(StageRun.pipeline_run_id == run.id))).all()
    )
    for stage in stages:
        stage.status = StageRunStatus.PENDING.value
        stage.executor_reference = celery_task_id
        stage.started_at = None
        stage.completed_at = None
        stage.error_message = None
        if stage.stage_code == "DATA_PROCESSING":
            stage.input_payload = {**(stage.input_payload or {}), "csv": upload_metadata}
    await db.commit()

    try:
        process_pipeline_run.apply_async(
            args=[run.id, upload_metadata["storage_key"]],
            task_id=celery_task_id,
        )
    except Exception as exc:
        run.status = PipelineRunStatus.FAILED.value
        run.current_stage = "DISPATCH"
        run.completed_at = utcnow()
        run.updated_at = run.completed_at
        await db.commit()
        raise DomainException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "PIPELINE_DISPATCH_FAILED",
            "CSV 처리 작업을 발행하지 못했습니다.",
        ) from exc
    return celery_task_id


async def get_result_artifact(db: AsyncSession, run_id: int) -> Artifact:
    run = await db.get(PipelineRun, run_id)
    if run is None:
        raise not_found("PIPELINE_RUN_NOT_FOUND", "파이프라인 실행을 찾을 수 없습니다.")
    artifact = await db.scalar(
        select(Artifact)
        .where(Artifact.pipeline_run_id == run_id, Artifact.artifact_type == "FINAL")
        .order_by(Artifact.created_at.desc(), Artifact.id.desc())
    )
    if artifact is None:
        raise not_found("PIPELINE_RESULT_NOT_READY", "결과 CSV가 아직 준비되지 않았습니다.")
    return artifact
