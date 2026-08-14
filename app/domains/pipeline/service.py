import asyncio
import logging
from uuid import uuid4
import hashlib
import json
import re
import secrets

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import DomainException, conflict, forbidden, not_found, unauthorized
from app.common.pii.guard import guard_text
from app.common.time_utils import utcnow
from app.core.config import settings
from app.domains.pipeline.model import (
    ApiKeyStatus,
    ApiUsageLog,
    Artifact,
    Client,
    Contract,
    ContractApiKey,
    ContractStatus,
    DataRequest,
    DataRequestStatus,
    Delivery,
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
    EmailDelivery,
    EmailDeliveryStatus,
)
from app.domains.dashboard.model import TaskViewSnapshot
from app.domains.employees.model import Employee, PermissionCode
from app.domains.pipeline.schema import (
    CreateDataRequestRequest,
    CreateDataRequestResponse,
    CreateEmailDeliveryRequest,
    EmailDeliveryResponse,
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


def _schedule_pipeline_execution(run_id: int, execution_id: str) -> None:
    """Schedule direct AgentCore execution after the DB transaction commits."""
    from app.domains.pipeline.executor import run_pipeline_stage_direct

    asyncio.create_task(run_pipeline_stage_direct(run_id))


def _make_request_no() -> str:
    return f"REQ-{utcnow():%Y%m%d}-{uuid4().hex[:6].upper()}"


def result_download_filename(request_no: str, run_id: int) -> str:
    """실무자가 작업과 재가공 실행을 함께 식별할 수 있는 결과 파일명(내부용 CSV 다운로드 버튼)."""
    return f"{request_no}-run-{run_id}-result.csv"


_FILENAME_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t\x00]')


def _sanitize_filename_component(value: str, *, max_length: int = 60) -> str:
    cleaned = _FILENAME_UNSAFE_CHARS.sub("", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_length].strip()


def customer_result_filename(client_company_name: str, request_title: str, request_no: str) -> str:
    """고객 수신 메일에 실을 파일명. 고객사·작업명이 있으면 그걸 쓰고, 없으면 요청번호로 대체한다."""
    parts = [
        _sanitize_filename_component(part)
        for part in (client_company_name, request_title)
        if part and part.strip()
    ]
    parts = [part for part in parts if part]
    base = "_".join(parts) if parts else request_no
    return f"{base}_최종산출물.csv"


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
    db: AsyncSession,
    payload: CreateDataRequestRequest,
    owner: Employee,
    actor_ip: str | None = None,
) -> CreateDataRequestResponse:
    # 무엇을 만들기 전에 검사한다. HITL 게이트에서 판단하면 이미 LLM 으로 나간 뒤다.
    await guard_text(
        payload.raw_requirement,
        source_table="data_requests",
        source_column="raw_requirement",
        source_endpoint="POST /api/v1/data-requests",
        confirmed=payload.confirm_pii,
        actor_employee_code=getattr(owner, "employee_code", None),
        actor_ip=actor_ip,
    )

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
    execution_id = str(uuid4())
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
        execution_id=execution_id,
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
            executor=settings.pipeline_execution_backend,
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
        _schedule_pipeline_execution(run.id, execution_id)
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
                message="파이프라인 실행 요청 발행에 실패했습니다.",
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
        execution_id=execution_id,
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
    client = (
        await db.get(Client, data_request.client_id) if data_request.client_id else None
    )
    return PipelineRunResponse(
        run_id=run.id,
        request_no=data_request.request_no,
        request_title=data_request.title,
        # 등록된 고객사 연락처가 있을 때만 채운다. 없으면 None으로 두고 화면이 빈 칸을
        # 보여준다(로그인 사용자 이메일로 대체하지 않는다).
        client_contact_email=(client.contact_email or None) if client else None,
        raw_requirement=data_request.raw_requirement,
        request_status=data_request.status,
        run_status=run.status,
        current_stage=run.current_stage,
        progress_percent=run.progress_percent,
        execution_id=run.execution_id,
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


async def _get_accessible_run(
    db: AsyncSession,
    run_id: int,
    employee: Employee,
    permissions: set[PermissionCode],
) -> tuple[PipelineRun, DataRequest]:
    """존재 여부를 노출하지 않으면서 실행 소유권을 검증한다."""
    run = await db.get(PipelineRun, run_id)
    if run is None:
        raise not_found("PIPELINE_RUN_NOT_FOUND", "파이프라인 실행을 찾을 수 없습니다.")
    data_request = await db.get(DataRequest, run.data_request_id)
    is_admin = PermissionCode.CONTRACT_MANAGE in permissions
    if data_request is None or (not is_admin and data_request.owner_id != employee.id):
        raise not_found("PIPELINE_RUN_NOT_FOUND", "파이프라인 실행을 찾을 수 없습니다.")
    return run, data_request


async def get_accessible_run(
    db: AsyncSession,
    run_id: int,
    employee: Employee,
    permissions: set[PermissionCode],
) -> tuple[PipelineRun, DataRequest]:
    """직원 인증과 run 소유권 검증을 외부 라우터에서도 재사용한다."""
    return await _get_accessible_run(db, run_id, employee, permissions)


async def _get_completed_selection_stage(db: AsyncSession, run_id: int) -> StageRun:
    stage = await db.scalar(
        select(StageRun)
        .where(
            StageRun.pipeline_run_id == run_id,
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
    return stage


def _validated_sample_output(stage: StageRun) -> dict:
    output = stage.output_payload or {}
    columns = output.get("sample_columns")
    rows = output.get("sample_rows")
    metadata = output.get("sample_metadata")
    if (
        not isinstance(columns, list)
        or not columns
        or not isinstance(rows, list)
        or len(rows) != 5
        or not isinstance(metadata, dict)
        or metadata.get("is_synthetic") is not True
    ):
        raise DomainException(
            status.HTTP_409_CONFLICT,
            "PIPELINE_SAMPLE_INVALID",
            "저장된 데이터 선별 결과에 유효한 합성 샘플 5건이 없습니다.",
        )
    return output


async def get_sample_preview(
    db: AsyncSession,
    run_id: int,
    employee: Employee,
    permissions: set[PermissionCode],
) -> SamplePreviewResponse:
    """가장 최근에 완료된 데이터 선별 단계의 합성 샘플을 반환한다."""
    run, _ = await _get_accessible_run(db, run_id, employee, permissions)
    stage = await _get_completed_selection_stage(db, run.id)
    output = _validated_sample_output(stage)
    columns = output.get("sample_columns")
    rows = output.get("sample_rows")
    metadata = output.get("sample_metadata")

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


def _normalize_recipient(value: str) -> str:
    local, separator, domain = value.strip().rpartition("@")
    return f"{local}@{domain.lower()}" if separator else value.strip().lower()


async def validate_customer_api_credentials(
    db: AsyncSession,
    run: PipelineRun,
    endpoint_url: str,
    raw_key: str,
) -> None:
    """메일에 실을 API 인증정보가 같은 run의 활성 계약 키인지 확인한다."""
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    row = await db.execute(
        select(ContractApiKey, Contract)
        .join(Contract, Contract.id == ContractApiKey.contract_id)
        .where(ContractApiKey.key_hash == key_hash)
    )
    api_key, contract = row.one_or_none() or (None, None)
    now = utcnow()
    if api_key is None or contract is None:
        raise conflict("INVALID_EMAIL_API_CREDENTIALS", "메일에 사용할 API 인증정보가 유효하지 않습니다.")
    if api_key.status != ApiKeyStatus.ACTIVE.value or (
        api_key.expires_at is not None and api_key.expires_at < now
    ):
        raise conflict("INVALID_EMAIL_API_CREDENTIALS", "메일에 사용할 API Key가 만료되었거나 폐기되었습니다.")
    if contract.data_request_id != run.data_request_id:
        raise conflict("INVALID_EMAIL_API_CREDENTIALS", "API Key가 현재 산출물의 계약에 속하지 않습니다.")
    expected_url = f"{settings.customer_api_base_url}/api/external/v1/deliveries/{contract.contract_no}"
    if endpoint_url != expected_url:
        raise conflict("INVALID_EMAIL_API_CREDENTIALS", "API URL이 현재 산출물의 계약 URL과 일치하지 않습니다.")


async def create_email_delivery(
    db: AsyncSession,
    run_id: int,
    payload: CreateEmailDeliveryRequest,
    idempotency_key: str,
    employee: Employee,
    permissions: set[PermissionCode],
) -> EmailDeliveryResponse:
    """발송 요청을 FastAPI DB에 QUEUED로 기록한다.

    큐 발행은 라우터의 adapter가 담당하므로, 이 함수는 DB 상태와 멱등성
    계약만 책임진다.
    """
    key = idempotency_key.strip()
    if not key or len(key) > 255:
        raise DomainException(status.HTTP_400_BAD_REQUEST, "INVALID_IDEMPOTENCY_KEY", "Idempotency-Key가 필요합니다.")

    run, _ = await _get_accessible_run(db, run_id, employee, permissions)

    if payload.delivery_type == "FINAL_ARTIFACT":
        await validate_customer_api_credentials(db, run, payload.api_endpoint_url or "", payload.api_key or "")
        if settings.artifact_storage_backend != "s3":
            # Spring이 storage_key로 S3 presigned URL을 만든다. 로컬 백엔드로 저장된
            # 산출물은 S3에 없으므로 여기서 막지 않으면 링크가 항상 NoSuchKey로 깨진다.
            raise DomainException(
                status.HTTP_409_CONFLICT,
                "ARTIFACT_STORAGE_NOT_EMAILABLE",
                "이 산출물은 로컬 스토리지에 저장되어 메일로 발송할 수 없습니다. CSV 다운로드를 이용해주세요.",
            )
        artifact = await get_result_artifact(db, run.id)
        stage_run = await db.get(StageRun, artifact.stage_run_id) if artifact.stage_run_id else None
        attempt_no = stage_run.attempt_no if stage_run else 1
        content_sha256 = artifact.checksum or ""
    else:
        preview = await get_sample_preview(db, run.id, employee, permissions)
        if payload.delivery_type == "SELECTION_SAMPLE" and len(preview.rows) != 5:
            raise DomainException(status.HTTP_409_CONFLICT, "PIPELINE_SAMPLE_INVALID", "샘플 데이터는 정확히 5건이어야 합니다.")
        attempt_no = preview.attempt_no
        sample_document = {"columns": [column.model_dump() for column in preview.columns], "rows": preview.rows}
        content_sha256 = hashlib.sha256(
            json.dumps(sample_document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    recipient = _normalize_recipient(payload.recipient)
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "run_id": run.id,
                "type": payload.delivery_type,
                "recipient": recipient,
                "content_sha256": content_sha256,
                "template": payload.template_version,
                "api_endpoint_url": payload.api_endpoint_url or "",
                "api_key": payload.api_key or "",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    existing = await db.scalar(select(EmailDelivery).where(EmailDelivery.idempotency_key == key))
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise DomainException(status.HTTP_409_CONFLICT, "IDEMPOTENCY_KEY_REUSED", "Idempotency-Key가 다른 요청에 사용되었습니다.")
        return EmailDeliveryResponse.model_validate(existing, from_attributes=True)

    active = await db.scalar(
        select(EmailDelivery).where(
            EmailDelivery.run_id == run.id,
            EmailDelivery.stage_attempt_no == attempt_no,
            EmailDelivery.recipient_normalized == recipient,
            EmailDelivery.delivery_type == payload.delivery_type,
            EmailDelivery.status.in_([EmailDeliveryStatus.QUEUED.value, EmailDeliveryStatus.SENDING.value]),
        )
    )
    if active is not None:
        return EmailDeliveryResponse.model_validate(active, from_attributes=True)

    now = utcnow()
    delivery = EmailDelivery(
        run_id=run.id,
        stage_attempt_no=attempt_no,
        requested_by=employee.id,
        delivery_type=payload.delivery_type,
        recipient=payload.recipient.strip(),
        recipient_normalized=recipient,
        status=EmailDeliveryStatus.QUEUED.value,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        sample_sha256=content_sha256,
        template_version=payload.template_version,
        created_at=now,
        updated_at=now,
    )
    db.add(delivery)
    await db.flush()
    # 큐 발행 전에 요청 레코드를 내구화한다. 발행 시 응답이 유실되어도
    # 재시작 복구 잡이 QUEUED 행을 다시 찾을 수 있다.
    await db.commit()
    return EmailDeliveryResponse.model_validate(delivery, from_attributes=True)


async def get_email_delivery(
    db: AsyncSession,
    run_id: int,
    delivery_id: str,
    employee: Employee,
    permissions: set[PermissionCode],
) -> EmailDeliveryResponse:
    """발송 상태 폴링/SSE 스냅샷 조회용. run 접근 권한을 재확인한다."""
    run, _ = await _get_accessible_run(db, run_id, employee, permissions)
    delivery = await db.scalar(
        select(EmailDelivery).where(
            EmailDelivery.delivery_id == delivery_id,
            EmailDelivery.run_id == run.id,
        )
    )
    if delivery is None:
        raise not_found("EMAIL_DELIVERY_NOT_FOUND", "이메일 발송 요청을 찾을 수 없습니다.")
    return EmailDeliveryResponse.model_validate(delivery, from_attributes=True)


logger = logging.getLogger(__name__)

EMAIL_QUEUE_UNAVAILABLE_CODE = "EMAIL_QUEUE_NOT_CONFIGURED"


async def mark_email_delivery_unavailable(
    db: AsyncSession,
    delivery_id: str,
) -> EmailDeliveryResponse:
    """발송 큐가 없어 아무도 소비할 수 없는 요청을 terminal 상태로 확정한다.

    QUEUED로 남기면 화면은 상태 변화를 기다리며 무한히 대기한다. 발송 경로가 구성되기
    전까지는 실패로 명확히 알리는 편이 정확하다. 큐를 붙인 뒤에는 이 경로 자체가
    실행되지 않는다(publish_email_delivery가 True를 반환).
    """
    delivery = await db.scalar(
        select(EmailDelivery).where(EmailDelivery.delivery_id == delivery_id)
    )
    if delivery is None:
        raise not_found("EMAIL_DELIVERY_NOT_FOUND", "이메일 발송 요청을 찾을 수 없습니다.")
    delivery.status = EmailDeliveryStatus.FAILED.value
    delivery.failure_code = EMAIL_QUEUE_UNAVAILABLE_CODE
    delivery.updated_at = utcnow()
    await db.commit()
    await db.refresh(delivery)
    logger.warning(
        "Email delivery queue is not configured; marking delivery as failed: %s",
        delivery_id,
    )
    return EmailDeliveryResponse.model_validate(delivery, from_attributes=True)


async def get_email_delivery_context(
    db: AsyncSession,
    run_id: int,
    employee: Employee,
    permissions: set[PermissionCode],
) -> dict:
    """메일 템플릿에 채울 요청/고객사/담당자 정보. 값이 없으면 빈 문자열로 내려서
    Spring 쪽이 항상 같은 필드 계약으로 렌더링할 수 있게 한다.
    """
    run, data_request = await _get_accessible_run(db, run_id, employee, permissions)
    client = await db.get(Client, data_request.client_id)
    owner = await db.get(Employee, data_request.owner_id) if data_request.owner_id else None
    return {
        "request_no": data_request.request_no,
        "request_title": data_request.title,
        "client_company_name": (client.company_name if client else "") or "",
        "owner_name": data_request.owner_name or (owner.name if owner else "") or "",
        "owner_email": (owner.email if owner else "") or "",
    }


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
    actor_ip: str | None = None,
) -> StageReviewResponse:
    """단계 산출물에 대한 사람 검토(HITL)를 반영한다.

    승인이면 다음 단계를 dispatch하고, 마지막 단계(DATA_PROCESSING) 승인이면 run을
    COMPLETED로 마감한다. 반려면 실패 사유에 따라 되돌아갈 단계를 정해서 그 단계부터
    재시도할 StageRun 행을 새 attempt로 만든다.
    """
    # 검토 의견도 그대로 저장돼 다음 단계 프롬프트에 실린다. 요구사항과 같은 기준으로 검사한다.
    #
    # source 를 reviews 가 아니라 pipeline_runs 로 잡는 이유: 값이 저장되는 곳은
    # reviews.feedback 이지만 그 행은 이 검사를 통과한 뒤에 만들어진다. reviews.id 를
    # 쓰면 항상 NULL 이 되어 (source_table, source_id) 인덱스가 무의미해진다. 실제로
    # 조회 가능한 조합인 run_id 를 남기고, 어느 필드였는지는 source_column 에 적는다.
    await guard_text(
        payload.feedback,
        source_table="pipeline_runs",
        source_column="reviews.feedback",
        source_endpoint="POST /api/v1/runs/{run_id}/review",
        confirmed=payload.confirm_pii,
        source_id=run_id,
        actor_employee_code=getattr(reviewer, "employee_code", None),
        actor_ip=actor_ip,
    )

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
        execution_id = await _redispatch(db, run, now)
        return StageReviewResponse(
            run_id=run.id,
            reviewed_stage=reviewed_stage.value,
            decision=ReviewDecision.CHANGES_REQUESTED,
            run_status=PipelineRunStatus.QUEUED,
            next_stage=target.value,
            rollback_to_stage=target.value,
            execution_id=execution_id,
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
        execution_id = await _redispatch(db, run, now)
        return StageReviewResponse(
            run_id=run.id,
            reviewed_stage=reviewed_stage.value,
            decision=decision,
            run_status=PipelineRunStatus.QUEUED,
            next_stage=target.value,
            rollback_to_stage=target.value,
            execution_id=execution_id,
        )

    if reviewed_stage == StageName.REQUIREMENT_ANALYSIS:
        if stage_run is None:
            raise DomainException(
                status.HTTP_409_CONFLICT,
                "PIPELINE_REQUIREMENT_STAGE_NOT_FOUND",
                "승인할 요구사항 분석 결과를 찾을 수 없습니다.",
            )
        stage_run.output_payload = {
            **(stage_run.output_payload or {}),
            **(
                {"delivery_channel": payload.delivery_channel}
                if payload.delivery_channel is not None
                else {}
            ),
            # 전달 기능은 현재 CSV 단일 산출물만 제공한다. 이전 클라이언트가
            # output_formats를 보내더라도 요청값을 신뢰하지 않는다.
            "output_formats": ["csv"],
        }
        data_request = await db.get(DataRequest, run.data_request_id)
        if data_request is not None:
            if payload.delivery_channel is not None:
                data_request.delivery_channels = [payload.delivery_channel]
            data_request.output_formats = ["csv"]
            data_request.updated_at = now

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
            execution_id=run.execution_id,
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
    execution_id = await _redispatch(db, run, now)
    return StageReviewResponse(
        run_id=run.id,
        reviewed_stage=reviewed_stage.value,
        decision=decision,
        run_status=PipelineRunStatus.QUEUED,
        next_stage=next_stage.value,
        rollback_to_stage=None,
        execution_id=execution_id,
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
                executor=settings.pipeline_execution_backend,
                # 승인된 선별 계획 등 단계 입력은 재시도에서도 그대로 유지한다.
                input_payload=dict(previous.input_payload or {}) if previous else {},
                output_payload={},
                validation_result={},
                created_at=now,
            )
        )


async def _redispatch(db: AsyncSession, run: PipelineRun, now) -> str:
    """Supervisor를 다시 발행한다. run.execution_id를 새로 발급해서 stale 이벤트를 끊는다."""
    execution_id = str(uuid4())
    run.execution_id = execution_id
    await db.commit()

    try:
        _schedule_pipeline_execution(run.id, execution_id)
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
                message="파이프라인 실행 요청 발행에 실패했습니다.",
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
    return execution_id


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


async def issue_customer_api_key(
    db: AsyncSession,
    run_id: int,
    employee: Employee,
    permissions: set[PermissionCode],
) -> dict:
    """고객이 산출물을 API로 재다운로드할 수 있는 키를 발급한다.

    평문 키는 이 응답에서만 노출되고 DB에는 해시만 저장한다. 실제 조회·presign은
    Spring(고객 API)이 담당하므로, 여기서는 계약을 ACTIVE로 만들고 이번 산출물을
    가리키는 Delivery 행을 남겨서 Spring이 어떤 artifact를 내려줄지 찾을 수 있게 한다.
    """
    if settings.artifact_storage_backend != "s3":
        # Spring이 storage_key로 S3 presigned URL을 만든다. 로컬 백엔드로 저장된
        # 산출물은 S3에 없으므로 여기서 막지 않으면 발급된 키가 항상 NoSuchKey로 깨진다.
        raise DomainException(
            status.HTTP_409_CONFLICT,
            "ARTIFACT_STORAGE_NOT_API_READY",
            "이 산출물은 로컬 스토리지에 저장되어 API로 제공할 수 없습니다.",
        )
    run, data_request = await _get_accessible_run(db, run_id, employee, permissions)
    artifact = await get_result_artifact(db, run.id)

    now = utcnow()
    contract = await db.scalar(
        select(Contract)
        .where(Contract.data_request_id == data_request.id, Contract.status == ContractStatus.ACTIVE.value)
    )
    if contract is None:
        contract = await db.scalar(
            select(Contract)
            .where(Contract.data_request_id == data_request.id)
            .order_by(Contract.created_at.desc())
        )
    if contract is None:
        contract = Contract(
            data_request_id=data_request.id,
            contract_no=_make_contract_no(),
            status=ContractStatus.ACTIVE.value,
            signed_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(contract)
        await db.flush()
    elif contract.status != ContractStatus.ACTIVE.value:
        contract.status = ContractStatus.ACTIVE.value
        contract.signed_at = contract.signed_at or now
        contract.updated_at = now

    db.add(
        Delivery(
            data_request_id=data_request.id,
            artifact_id=artifact.id,
            channel="API",
            status="ACTIVE",
            created_at=now,
        )
    )

    raw_key = f"hnk_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    api_key = ContractApiKey(
        contract_id=contract.id,
        key_hash=key_hash,
        key_last4=raw_key[-4:],
        issued_at=now,
        status=ApiKeyStatus.ACTIVE.value,
        created_at=now,
    )
    db.add(api_key)
    await db.commit()

    return {
        "endpoint_url": f"{settings.customer_api_base_url}/api/external/v1/deliveries/{contract.contract_no}",
        "api_key": raw_key,
        "key_last4": api_key.key_last4,
        "contract_no": contract.contract_no,
    }


async def lookup_customer_delivery(db: AsyncSession, contract_no: str, raw_api_key: str) -> dict:
    """Spring의 고객 API가 대신 물어보는 창구. 여기서만 사내 DB(민감 스키마)를 만진다.

    Spring은 이 결과의 storage_key로 자기 S3 자격증명으로 직접 presign한다 —
    이 함수는 S3를 건드리지 않는다("S3 -> Spring" 원칙 유지). DB 접근은 이 함수
    하나로 좁혀서, 인터넷에 노출된 Spring이 사내 DB 자격증명을 갖지 않게 한다.
    """
    now = utcnow()
    key_hash = hashlib.sha256(raw_api_key.encode()).hexdigest()
    api_key = await db.scalar(select(ContractApiKey).where(ContractApiKey.key_hash == key_hash))
    if api_key is None:
        raise unauthorized("INVALID_API_KEY", "API Key가 유효하지 않습니다.")

    async def _log(response_status: int) -> None:
        db.add(
            ApiUsageLog(
                api_key_id=api_key.id,
                endpoint=f"/api/external/v1/deliveries/{contract_no}",
                response_status=response_status,
                response_time_ms=None,
                requested_at=now,
            )
        )
        await db.commit()

    if api_key.status != ApiKeyStatus.ACTIVE.value:
        await _log(status.HTTP_403_FORBIDDEN)
        raise forbidden("API_KEY_REVOKED", "폐기된 API Key입니다.")
    if api_key.expires_at is not None and api_key.expires_at < now:
        await _log(status.HTTP_403_FORBIDDEN)
        raise forbidden("API_KEY_EXPIRED", "만료된 API Key입니다.")

    contract = await db.scalar(select(Contract).where(Contract.contract_no == contract_no))
    if contract is None:
        await _log(status.HTTP_404_NOT_FOUND)
        raise not_found("CONTRACT_NOT_FOUND", "계약을 찾을 수 없습니다.")
    if contract.id != api_key.contract_id:
        await _log(status.HTTP_403_FORBIDDEN)
        raise forbidden("API_KEY_CONTRACT_MISMATCH", "이 계약에 속하지 않은 API Key입니다.")

    delivery = await db.scalar(
        select(Delivery)
        .where(Delivery.data_request_id == contract.data_request_id, Delivery.channel == "API")
        .order_by(Delivery.id.desc())
    )
    if delivery is None:
        await _log(status.HTTP_404_NOT_FOUND)
        raise not_found("DELIVERY_NOT_FOUND", "발급된 산출물이 아직 없습니다.")
    artifact = await db.get(Artifact, delivery.artifact_id)
    if artifact is None:
        await _log(status.HTTP_404_NOT_FOUND)
        raise not_found("ARTIFACT_NOT_FOUND", "산출물 파일을 찾을 수 없습니다.")

    data_request = await db.get(DataRequest, contract.data_request_id)
    client = await db.get(Client, data_request.client_id) if data_request else None
    filename = customer_result_filename(
        client.company_name if client else "",
        data_request.title if data_request else "",
        contract.contract_no,
    )

    await _log(status.HTTP_200_OK)
    return {
        "storage_key": artifact.storage_key,
        "mime_type": artifact.mime_type or "text/csv",
        "artifact_filename": filename,
    }
