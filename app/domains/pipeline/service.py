from uuid import uuid4

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import DomainException, not_found
from app.common.time_utils import utcnow
from app.domains.pipeline.model import (
    Artifact,
    Client,
    Contract,
    ContractStatus,
    DataRequest,
    DataRequestStatus,
    EventType,
    FailureCode,
    PipelineEvent,
    PipelineRun,
    PipelineRunStatus,
    Review,
    ReviewDecision,
    StageName,
    StageRun,
    StageRunStatus,
)
from app.domains.dashboard.model import TaskViewSnapshot
from app.domains.employees.model import Employee
from app.domains.pipeline.schema import (
    CreateDataRequestRequest,
    CreateDataRequestResponse,
    PipelineRunResponse,
    ProcessingResultResponse,
    RequirementAnalysisResponse,
    RunEventResponse,
    RunStageResponse,
    SamplePreviewResponse,
    StageReviewRequest,
    StageReviewResponse,
)
from app.domains.pipeline.failure import public_failure
from app.domains.pipeline.plan_integrity import (
    selection_plan_sha256,
    snapshot_selection_plan,
)
from app.domains.pipeline.supervisor import HITL_GATE, STAGE_ORDER, rollback_target
from app.worker.tasks import process_pipeline_run


def _make_request_no() -> str:
    return f"REQ-{utcnow():%Y%m%d}-{uuid4().hex[:6].upper()}"


def _make_contract_no() -> str:
    return f"CTR-{utcnow():%Y%m%d}-{uuid4().hex[:6].upper()}"


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
    db: AsyncSession, payload: CreateDataRequestRequest, owner: Employee
) -> CreateDataRequestResponse:
    now = utcnow()
    client_payload = payload.client
    company_name = client_payload.company_name if client_payload is not None else payload.requester_name
    client = await db.scalar(select(Client).where(Client.company_name == company_name))
    if client is None:
        client = Client(
            company_name=company_name,
            business_registration_number=client_payload.business_registration_number if client_payload else None,
            contact_name=client_payload.contact_name if client_payload else None,
            contact_email=client_payload.contact_email if client_payload else None,
            contact_phone=client_payload.contact_phone if client_payload else None,
            created_at=now,
            updated_at=now,
        )
        db.add(client)
        await db.flush()
    elif client_payload is not None:
        client.business_registration_number = client_payload.business_registration_number
        client.contact_name = client_payload.contact_name
        client.contact_email = client_payload.contact_email
        client.contact_phone = client_payload.contact_phone
        client.updated_at = now

    title = payload.title or payload.raw_requirement.strip().splitlines()[0][:200]
    celery_task_id = str(uuid4())
    data_request = DataRequest(
        request_no=_make_request_no(),
        client_id=client.id,
        owner_id=owner.id,
        owner_name=owner.name,
        requester_name=company_name,
        title=title,
        raw_requirement=payload.raw_requirement.strip(),
        output_formats=["CSV", "XLSX"],
        delivery_channels=["FILE_DOWNLOAD"],
        data_sensitivity=payload.data_sensitivity,
        analysis_condition={"async_pipeline": True},
        status=DataRequestStatus.QUEUED,
        created_at=now,
        updated_at=now,
    )
    db.add(data_request)
    await db.flush()

    if payload.contract is not None:
        contract = Contract(
            data_request_id=data_request.id,
            contract_no=payload.contract.contract_no or _make_contract_no(),
            start_date=payload.contract.start_date,
            end_date=payload.contract.end_date,
            delivery_due_at=payload.contract.delivery_due_at,
            status=ContractStatus.DRAFT.value,
            created_at=now,
            updated_at=now,
        )
        db.add(contract)

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
        contract_no=contract.contract_no if payload.contract is not None else None,
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
    requirement_stage = next(
        (
            stage
            for stage in reversed(stages)
            if stage.stage_code == StageName.REQUIREMENT_ANALYSIS.value
            and stage.status == StageRunStatus.COMPLETED.value
        ),
        None,
    )
    requirement_analysis = (
        RequirementAnalysisResponse.model_validate(requirement_stage.output_payload)
        if requirement_stage is not None
        else None
    )
    latest_failed_stage = next(
        (stage for stage in reversed(stages) if stage.status == StageRunStatus.FAILED.value),
        None,
    )
    run_failure = (
        public_failure(
            stage=latest_failed_stage.stage_code,
            result=latest_failed_stage.output_payload,
            validation_result=latest_failed_stage.validation_result,
            rollback_to_stage=run.rollback_to_stage,
            error_message=latest_failed_stage.error_message or run.error_message,
        )
        if latest_failed_stage is not None
        else None
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
        error_message=run.error_message,
        failure=run_failure,
        stages=[
            RunStageResponse(
                stage_code=stage.stage_code,
                status=stage.status,
                executor=stage.executor,
                created_at=stage.created_at,
                failure=public_failure(
                    stage=stage.stage_code,
                    result=stage.output_payload,
                    validation_result=stage.validation_result,
                    rollback_to_stage=run.rollback_to_stage,
                    error_message=stage.error_message,
                ) if stage.status == StageRunStatus.FAILED.value else None,
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
        requirement_analysis=requirement_analysis,
    )


async def get_sample_preview(db: AsyncSession, run_id: int) -> SamplePreviewResponse:
    """가장 최근에 완료된 데이터 선별 단계의 합성 샘플을 반환한다."""
    run = await db.get(PipelineRun, run_id)
    if run is None:
        raise not_found("PIPELINE_RUN_NOT_FOUND", "파이프라인 실행을 찾을 수 없습니다.")

    stage = await db.scalar(
        select(StageRun)
        .where(
            StageRun.pipeline_run_id == run.id,
            StageRun.stage_code == StageName.DATA_SELECTION.value,
            StageRun.status == StageRunStatus.COMPLETED.value,
        )
        .order_by(StageRun.attempt_no.desc(), StageRun.id.desc())
        .limit(1)
    )
    if stage is None:
        raise not_found(
            "PIPELINE_SAMPLE_NOT_READY",
            "데이터 선별 샘플이 아직 준비되지 않았습니다.",
        )

    output = stage.output_payload or {}
    columns = output.get("sample_columns")
    rows = output.get("sample_rows")
    metadata = output.get("sample_metadata")
    if (
        not isinstance(columns, list)
        or not columns
        or not isinstance(rows, list)
        or not isinstance(metadata, dict)
    ):
        raise DomainException(
            status.HTTP_409_CONFLICT,
            "PIPELINE_SAMPLE_INVALID",
            "저장된 데이터 선별 결과에 유효한 샘플이 없습니다.",
        )

    interpretations = output.get("interpretations") or []
    catalog_issues = output.get("catalog_issues") or []
    catalog_matches = output.get("catalog_matches") or []
    confirmation_terms = list(
        dict.fromkeys(
            str(item["term"])
            for item in [*interpretations, *catalog_matches]
            if isinstance(item, dict)
            and item.get("term")
            and item.get("requires_confirmation") is True
        )
    )
    return SamplePreviewResponse(
        run_id=run.id,
        stage=StageName.DATA_SELECTION.value,
        attempt_no=stage.attempt_no,
        columns=columns,
        rows=rows,
        metadata=metadata,
        selected_tables=output.get("selected_tables") or [],
        source_columns=output.get("source_columns") or [],
        derived_columns=output.get("derived_columns") or [],
        selection_query=output.get("selection_query") or {},
        interpretations=interpretations,
        catalog_issues=catalog_issues,
        catalog_matches=catalog_matches,
        review_summary={
            "requires_confirmation": bool(confirmation_terms),
            "confirmation_terms": confirmation_terms,
            "has_catalog_issues": bool(catalog_issues),
            "catalog_issue_count": len(catalog_issues),
        },
    )


async def get_processing_result(db: AsyncSession, run_id: int) -> ProcessingResultResponse:
    """가장 최근에 완료된 데이터 가공 단계의 JSON 산출물을 반환한다."""
    run = await db.get(PipelineRun, run_id)
    if run is None:
        raise not_found("PIPELINE_RUN_NOT_FOUND", "파이프라인 실행을 찾을 수 없습니다.")

    stage = await db.scalar(
        select(StageRun)
        .where(
            StageRun.pipeline_run_id == run.id,
            StageRun.stage_code == StageName.DATA_PROCESSING.value,
            StageRun.status == StageRunStatus.COMPLETED.value,
        )
        .order_by(StageRun.attempt_no.desc(), StageRun.id.desc())
        .limit(1)
    )
    if stage is None:
        raise not_found(
            "PIPELINE_RESULT_NOT_READY",
            "데이터 가공 결과가 아직 준비되지 않았습니다.",
        )

    output = stage.output_payload or {}
    required = (
        "api_result",
        "processed_columns",
        "quality_report",
        "processing_explanation",
    )
    if any(key not in output for key in required):
        raise DomainException(
            status.HTTP_409_CONFLICT,
            "PIPELINE_RESULT_INVALID",
            "저장된 데이터 가공 결과가 유효하지 않습니다.",
        )

    return ProcessingResultResponse(
        run_id=run.id,
        stage=StageName.DATA_PROCESSING.value,
        attempt_no=stage.attempt_no,
        api_result=output["api_result"],
        processed_columns=output["processed_columns"],
        quality_report=output["quality_report"],
        processing_explanation=output["processing_explanation"],
        visualization=output.get("visualization"),
        report=output.get("report"),
        processing_plan=output.get("processing_plan"),
    )


_REVIEW_TYPE_FOR_GATE = {
    PipelineRunStatus.WAITING_REQUIREMENT_REVIEW: "REQUIREMENT",
    PipelineRunStatus.WAITING_SAMPLE_REVIEW: "SAMPLE",
    PipelineRunStatus.WAITING_FINAL_REVIEW: "FINAL",
}
_GATE_TO_STAGE = {gate: stage for stage, gate in HITL_GATE.items()}


async def submit_stage_review(
    db: AsyncSession,
    run_id: int,
    reviewer: Employee,
    payload: StageReviewRequest,
) -> StageReviewResponse:
    """단계 산출물에 대한 사람 검토(HITL)를 반영한다.

    승인이면 다음 단계를 dispatch하고, 마지막 단계(DATA_PROCESSING) 승인이면 run을
    COMPLETED로 마감한다. 반려면 실패 사유에 따라 되돌아갈 단계를 정해서 그 단계부터
    재시도할 StageRun 행을 새 attempt로 만든다.
    """
    run = await db.get(PipelineRun, run_id)
    if run is None:
        raise not_found("PIPELINE_RUN_NOT_FOUND", "파이프라인 실행을 찾을 수 없습니다.")

    gate = PipelineRunStatus(run.status)
    is_failed_retry = gate == PipelineRunStatus.FAILED and payload.retry
    if gate not in _GATE_TO_STAGE and not is_failed_retry:
        raise DomainException(
            status.HTTP_409_CONFLICT,
            "PIPELINE_RUN_NOT_UNDER_REVIEW",
            "현재 실행 상태는 검토 대기 상태가 아닙니다.",
        )
    if is_failed_retry:
        retry_stage_code = run.rollback_to_stage or run.current_stage
        try:
            reviewed_stage = StageName(retry_stage_code or "")
        except ValueError as exc:
            raise DomainException(
                status.HTTP_409_CONFLICT,
                "PIPELINE_RETRY_TARGET_NOT_FOUND",
                "실패한 작업의 재시작 단계를 확인할 수 없습니다.",
            ) from exc
    else:
        reviewed_stage = _GATE_TO_STAGE[gate]

    now = utcnow()
    stage_run = await db.scalar(
        select(StageRun)
        .where(
            StageRun.pipeline_run_id == run.id,
            StageRun.stage_code == reviewed_stage.value,
        )
        .order_by(StageRun.attempt_no.desc(), StageRun.id.desc())
        .limit(1)
    )
    decision = ReviewDecision.APPROVED if payload.approved else ReviewDecision.CHANGES_REQUESTED
    db.add(
        Review(
            data_request_id=run.data_request_id,
            stage_run_id=stage_run.id if stage_run else None,
            reviewer_id=reviewer.id,
            reviewer_name=reviewer.name,
            review_type=(
                _REVIEW_TYPE_FOR_GATE[gate]
                if not is_failed_retry
                else {
                    StageName.REQUIREMENT_ANALYSIS: "REQUIREMENT",
                    StageName.DATA_SELECTION: "SAMPLE",
                    StageName.DATA_PROCESSING: "FINAL",
                }[reviewed_stage]
            ),
            decision=decision.value,
            feedback=payload.feedback,
            created_at=now,
        )
    )

    if is_failed_retry:
        target = StageName(run.rollback_to_stage or reviewed_stage.value)
        await _reopen_from(db, run, target, now)
        run.status = PipelineRunStatus.QUEUED.value
        run.current_stage = target.value
        run.progress_percent = 0
        run.rollback_to_stage = target.value
        run.error_message = None
        run.completed_at = None
        run.updated_at = now
        celery_task_id = await _redispatch(db, run, now)
        return StageReviewResponse(
            run_id=run.id,
            reviewed_stage=reviewed_stage.value,
            decision=ReviewDecision.CHANGES_REQUESTED,
            run_status=PipelineRunStatus.QUEUED,
            next_stage=target.value,
            rollback_to_stage=target.value,
            celery_task_id=celery_task_id,
        )

    if not payload.approved:
        if gate == PipelineRunStatus.WAITING_SAMPLE_REVIEW and payload.failure_code is None:
            # 합성 샘플의 의미 해석이 고객 의도와 다르면 승인된 요구사항 분석은
            # 유지하고, 자연어 feedback을 전달해 선별 단계만 다시 생성한다.
            target = StageName.DATA_SELECTION
        elif gate == PipelineRunStatus.WAITING_FINAL_REVIEW and payload.failure_code is None:
            # 최종 산출물 수정 요청은 승인된 요구사항과 선별 계획을 유지하고
            # 자연어 feedback을 전달해 가공 단계만 다시 실행한다.
            target = StageName.DATA_PROCESSING
        else:
            target = rollback_target(
                payload.failure_code.value
                if payload.failure_code
                else FailureCode.HUMAN_REJECTED.value
            )
        await _reopen_from(db, run, target, now)
        run.status = PipelineRunStatus.QUEUED.value
        run.current_stage = target.value
        run.progress_percent = 0
        run.rollback_to_stage = target.value
        run.error_message = payload.feedback or "검토자가 산출물을 반려했습니다."
        run.completed_at = None
        run.updated_at = now
        celery_task_id = await _redispatch(db, run, now)
        return StageReviewResponse(
            run_id=run.id,
            reviewed_stage=reviewed_stage.value,
            decision=decision,
            run_status=PipelineRunStatus.QUEUED,
            next_stage=target.value,
            rollback_to_stage=target.value,
            celery_task_id=celery_task_id,
        )

    if reviewed_stage == STAGE_ORDER[-1]:
        run.status = PipelineRunStatus.COMPLETED.value
        run.current_stage = reviewed_stage.value
        run.progress_percent = 100
        run.rollback_to_stage = None
        run.error_message = None
        run.completed_at = now
        run.updated_at = now
        data_request = await db.get(DataRequest, run.data_request_id)
        if data_request is not None:
            data_request.status = DataRequestStatus.COMPLETED.value
            data_request.updated_at = now
        await db.commit()
        return StageReviewResponse(
            run_id=run.id,
            reviewed_stage=reviewed_stage.value,
            decision=decision,
            run_status=PipelineRunStatus.COMPLETED,
            next_stage=None,
            rollback_to_stage=None,
            celery_task_id=run.celery_task_id,
        )

    next_stage = STAGE_ORDER[STAGE_ORDER.index(reviewed_stage) + 1]
    if reviewed_stage == StageName.DATA_SELECTION:
        if stage_run is None:
            raise DomainException(
                status.HTTP_409_CONFLICT,
                "PIPELINE_SELECTION_NOT_FOUND",
                "승인할 데이터 선별 결과를 찾을 수 없습니다.",
            )
        processing_stage = await db.scalar(
            select(StageRun)
            .where(
                StageRun.pipeline_run_id == run.id,
                StageRun.stage_code == StageName.DATA_PROCESSING.value,
                StageRun.status == StageRunStatus.PENDING.value,
            )
            .order_by(StageRun.attempt_no.desc(), StageRun.id.desc())
            .limit(1)
        )
        if processing_stage is None:
            raise DomainException(
                status.HTTP_409_CONFLICT,
                "PIPELINE_PROCESSING_STAGE_NOT_FOUND",
                "승인 계획을 연결할 데이터 가공 단계를 찾을 수 없습니다.",
            )
        approved_plan = snapshot_selection_plan(stage_run.output_payload or {})
        processing_stage.input_payload = {
            **(processing_stage.input_payload or {}),
            "approved_selection": {
                "stage_run_id": stage_run.id,
                "plan": approved_plan,
                "sha256": selection_plan_sha256(approved_plan),
                "approved_at": now.isoformat(),
                "reviewer_id": reviewer.id,
                "reviewer_name": reviewer.name,
            },
        }
    run.status = PipelineRunStatus.QUEUED.value
    run.current_stage = next_stage.value
    run.rollback_to_stage = None
    run.error_message = None
    run.updated_at = now
    celery_task_id = await _redispatch(db, run, now)
    return StageReviewResponse(
        run_id=run.id,
        reviewed_stage=reviewed_stage.value,
        decision=decision,
        run_status=PipelineRunStatus.QUEUED,
        next_stage=next_stage.value,
        rollback_to_stage=None,
        celery_task_id=celery_task_id,
    )


async def _reopen_from(
    db: AsyncSession,
    run: PipelineRun,
    target: StageName,
    now,
) -> None:
    """target 단계부터 뒤쪽 단계들을 ROLLED_BACK 처리하고 새 attempt 행을 만든다."""
    rollback_codes = [
        stage.value for stage in STAGE_ORDER[STAGE_ORDER.index(target):]
    ]
    stages = list(
        (
            await db.scalars(
                select(StageRun)
                .where(
                    StageRun.pipeline_run_id == run.id,
                    StageRun.stage_code.in_(rollback_codes),
                )
                .order_by(StageRun.attempt_no, StageRun.id)
            )
        ).all()
    )
    latest_by_code: dict[str, StageRun] = {}
    for stage in stages:
        latest_by_code[stage.stage_code] = stage
        if stage.status in {
            StageRunStatus.COMPLETED.value,
            StageRunStatus.PENDING.value,
            StageRunStatus.FAILED.value,
        }:
            stage.status = StageRunStatus.ROLLED_BACK.value

    for stage_code in rollback_codes:
        previous = latest_by_code.get(stage_code)
        db.add(
            StageRun(
                pipeline_run_id=run.id,
                retry_of_id=previous.id if previous else None,
                stage_code=stage_code,
                attempt_no=(previous.attempt_no if previous else 0) + 1,
                status=StageRunStatus.PENDING.value,
                executor="CELERY",
                # 승인된 선별 계획 등 단계 입력은 재시도에서도 그대로 유지한다.
                input_payload=dict(previous.input_payload or {}) if previous else {},
                output_payload={},
                validation_result={},
                created_at=now,
            )
        )


async def _redispatch(db: AsyncSession, run: PipelineRun, now) -> str:
    """Supervisor를 다시 발행한다. run.celery_task_id를 새로 발급해서 stale 이벤트를 끊는다."""
    celery_task_id = str(uuid4())
    run.celery_task_id = celery_task_id
    await db.commit()

    try:
        process_pipeline_run.apply_async(args=[run.id], task_id=celery_task_id)
    except Exception as exc:
        run.status = PipelineRunStatus.FAILED.value
        run.current_stage = "DISPATCH"
        run.error_message = "작업 큐에 요청을 발행하지 못했습니다."
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
