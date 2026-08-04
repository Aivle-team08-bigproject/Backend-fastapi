"""Front·Back Dashboard 연동 검증용 멱등 E2E 데이터 seed.

실행:
    python -m scripts.seed_frontend_e2e_data
"""

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from app.common.time_utils import utcnow
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal, engine
from app.domains.employees.model import (
    Department,
    Employee,
    EmployeePermission,
    EmployeeRole,
    EmployeeStatus,
    PermissionCode,
)
from app.domains.pipeline.model import (
    Client,
    DataRequest,
    DataRequestStatus,
    Artifact,
    ArtifactType,
    EventType,
    PipelineEvent,
    PipelineRun,
    PipelineRunStatus,
    PiiScanStatus,
    Review,
    ReviewDecision,
    StageRun,
    StageRunStatus,
)


PASSWORD = "E2E-Frontend!2026"
DEPARTMENT_NAME = "E2E 검증팀"

USERS = (
    ("E2E-PRACTITIONER-A", "E2E 실무자 A", EmployeeRole.GENERAL, set()),
    ("E2E-PRACTITIONER-B", "E2E 실무자 B", EmployeeRole.GENERAL, set()),
    ("E2E-MANAGER", "E2E 관리자", EmployeeRole.MANAGER, {PermissionCode.CONTRACT_MANAGE}),
    ("E2E-VIEWER", "E2E 조회 사용자", EmployeeRole.GENERAL, set()),
)

TASKS = (
    ("E2E-REQ-001", "E2E 고객사 A", "요구사항 승인 대기", "E2E-PRACTITIONER-A", "WAITING_REQUIREMENT_REVIEW", "REQUIREMENT_ANALYSIS", 0, 2),
    ("E2E-REQ-002", "E2E 고객사 B", "요구사항 분석 진행", "E2E-PRACTITIONER-A", "RUNNING", "REQUIREMENT_ANALYSIS", 10, 4),
    ("E2E-SAMPLE-001", "E2E 고객사 C", "샘플 데이터 승인 대기", "E2E-PRACTITIONER-A", "WAITING_SAMPLE_REVIEW", "DATA_SELECTION", 30, 3),
    ("E2E-SAMPLE-002", "E2E 고객사 D", "데이터 선별 진행", "E2E-PRACTITIONER-B", "RUNNING", "DATA_SELECTION", 30, 6),
    ("E2E-FINAL-001", "E2E 고객사 E", "최종 산출물 검토", "E2E-PRACTITIONER-B", "WAITING_FINAL_REVIEW", "DATA_PROCESSING", 60, 1),
    ("E2E-FINAL-002", "E2E 고객사 F", "데이터 가공 진행", "E2E-PRACTITIONER-B", "RUNNING", "DATA_PROCESSING", 60, 5),
    ("E2E-COMPLETE-001", "E2E 고객사 G", "완료 작업", "E2E-PRACTITIONER-A", "COMPLETED", "COMPLETED", 100, 10),
    ("E2E-FAIL-001", "E2E 고객사 H", "실패 작업", "E2E-PRACTITIONER-A", "FAILED", "DATA_PROCESSING", 60, 7),
    ("E2E-FAIL-002", "E2E 고객사 I", "반복 실패 작업", "E2E-PRACTITIONER-B", "FAILED", "DATA_PROCESSING", 60, 8),
    ("E2E-DEADLINE-001", "E2E 고객사 J", "마감 임박 작업", "E2E-PRACTITIONER-B", "RUNNING", "DATA_SELECTION", 30, 1),
    ("E2E-OVERDUE-001", "E2E 고객사 K", "기한 초과 작업", "E2E-PRACTITIONER-A", "RUNNING", "DATA_PROCESSING", 60, -2),
)


async def ensure_department(db, now: datetime) -> Department:
    department = await db.scalar(select(Department).where(Department.name == DEPARTMENT_NAME))
    if department is None:
        department = Department(
            name=DEPARTMENT_NAME,
            code="DEPT-E2E-VERIFY",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(department)
        await db.flush()
    return department


async def ensure_user(db, code: str, name: str, role: EmployeeRole, permissions: set[PermissionCode], department_id: int, now: datetime) -> Employee:
    employee = await db.scalar(select(Employee).where(Employee.employee_code == code))
    if employee is None:
        employee = Employee(
            employee_code=code,
            name=name,
            email=f"{code.lower()}@company.com",
            department_id=department_id,
            password_hash=hash_password(PASSWORD),
            status=EmployeeStatus.ACTIVE,
            role_code=role.value,
            must_change_password=False,
            failed_login_count=0,
            auth_version=1,
            created_by="E2E_SEED",
            created_at=now,
            updated_at=now,
        )
        db.add(employee)
        await db.flush()
    else:
        employee.name = name
        employee.password_hash = hash_password(PASSWORD)
        employee.status = EmployeeStatus.ACTIVE
        employee.role_code = role.value
        employee.must_change_password = False
        employee.department_id = department_id
        employee.updated_at = now
    await db.execute(delete(EmployeePermission).where(EmployeePermission.employee_id == employee.id))
    db.add_all([
        EmployeePermission(employee_id=employee.id, permission_code=permission)
        for permission in permissions
    ])
    return employee


async def seed() -> None:
    now = utcnow().replace(microsecond=0)
    async with AsyncSessionLocal() as db:
        department = await ensure_department(db, now)
        users = {
            code: await ensure_user(db, code, name, role, permissions, department.id, now)
            for code, name, role, permissions in USERS
        }
        await db.flush()

        for index, (request_no, company, title, assignee_code, run_status, stage_code, progress, due_days) in enumerate(TASKS):
            client = await db.scalar(select(Client).where(Client.company_name == company))
            if client is None:
                client = Client(company_name=company, created_at=now, updated_at=now)
                db.add(client)
                await db.flush()

            request = await db.scalar(select(DataRequest).where(DataRequest.request_no == request_no))
            due_at = now + timedelta(days=due_days)
            request_status = (
                DataRequestStatus.COMPLETED.value
                if run_status == PipelineRunStatus.COMPLETED.value
                else DataRequestStatus.FAILED.value
                if run_status == PipelineRunStatus.FAILED.value
                else DataRequestStatus.WAITING_REVIEW.value
                if run_status.startswith("WAITING_")
                else DataRequestStatus.RUNNING.value
            )
            values = {
                "client_id": client.id,
                "owner_id": users[assignee_code].id,
                "owner_name": users[assignee_code].name,
                "requester_name": company,
                "title": title,
                "business_purpose": title,
                "raw_requirement": f"{company} {title} 테스트 요구사항",
                "output_formats": ["CSV"],
                "delivery_channels": ["FILE_DOWNLOAD"],
                "analysis_condition": {
                    "e2e_seed": True,
                    "data_type": "E2E 검증 데이터",
                    "detail": title,
                    "due_at": due_at.isoformat(),
                },
                "status": request_status,
                "updated_at": now,
            }
            if request is None:
                request = DataRequest(request_no=request_no, created_at=now - timedelta(days=index), **values)
                db.add(request)
                await db.flush()
            else:
                for key, value in values.items():
                    setattr(request, key, value)

            run = await db.scalar(select(PipelineRun).where(PipelineRun.data_request_id == request.id, PipelineRun.attempt_no == 1))
            if run is None:
                run = PipelineRun(
                    data_request_id=request.id,
                    attempt_no=1,
                    status=run_status,
                    current_stage=stage_code,
                    progress_percent=progress,
                    created_at=request.created_at,
                    updated_at=now,
                    completed_at=now if run_status == PipelineRunStatus.COMPLETED.value else None,
                )
                db.add(run)
                await db.flush()
            else:
                run.status = run_status
                run.current_stage = stage_code
                run.progress_percent = progress
                run.updated_at = now

            stage_status = (
                StageRunStatus.COMPLETED.value
                if run_status in {"WAITING_REQUIREMENT_REVIEW", "WAITING_SAMPLE_REVIEW", "WAITING_FINAL_REVIEW", "COMPLETED"}
                else StageRunStatus.FAILED.value
                if run_status == "FAILED"
                else StageRunStatus.RUNNING.value
            )
            stage = await db.scalar(select(StageRun).where(StageRun.pipeline_run_id == run.id, StageRun.stage_code == stage_code, StageRun.attempt_no == 1))
            if stage is None:
                stage = StageRun(
                    pipeline_run_id=run.id,
                    stage_code=stage_code,
                    attempt_no=1,
                    status=stage_status,
                    executor="E2E_SEED",
                    input_payload={},
                    output_payload={"e2e_seed": True},
                    validation_result={"passed": stage_status == StageRunStatus.COMPLETED.value},
                    error_message="E2E 반복 실패 시나리오" if run_status == "FAILED" else None,
                    created_at=request.created_at,
                    started_at=request.created_at,
                    completed_at=now if stage_status == StageRunStatus.COMPLETED.value else None,
                )
                db.add(stage)
                await db.flush()
            if request_no == "E2E-FAIL-002":
                for attempt_no in (2, 3):
                    repeated_stage = await db.scalar(
                        select(StageRun).where(
                            StageRun.pipeline_run_id == run.id,
                            StageRun.stage_code == stage_code,
                            StageRun.attempt_no == attempt_no,
                        )
                    )
                    if repeated_stage is None:
                        db.add(StageRun(
                            pipeline_run_id=run.id,
                            stage_code=stage_code,
                            attempt_no=attempt_no,
                            status=StageRunStatus.FAILED.value,
                            executor="E2E_SEED",
                            input_payload={"retry_of": attempt_no - 1},
                            output_payload={},
                            validation_result={"passed": False},
                            error_message="E2E 반복 실패 시나리오",
                            created_at=now - timedelta(minutes=attempt_no),
                            started_at=now - timedelta(minutes=attempt_no),
                        ))

            if run_status in {"WAITING_SAMPLE_REVIEW", "WAITING_FINAL_REVIEW", "COMPLETED"}:
                artifact_type = (
                    ArtifactType.FINAL.value
                    if stage_code in {"DATA_PROCESSING", "COMPLETED"}
                    else ArtifactType.SAMPLE.value
                )
                storage_key = f"e2e/{request_no}/{artifact_type.lower()}.json"
                artifact = await db.scalar(select(Artifact).where(Artifact.storage_key == storage_key))
                if artifact is None:
                    artifact = Artifact(
                        pipeline_run_id=run.id,
                        stage_run_id=stage.id if stage is not None else None,
                        artifact_type=artifact_type,
                        storage_key=storage_key,
                        mime_type="application/json",
                        size_bytes=2048,
                        checksum=f"e2e-{request_no.lower()}",
                        pii_scan_status=PiiScanStatus.PASSED.value,
                        created_at=now,
                    )
                    db.add(artifact)
                    await db.flush()

                if run_status == "COMPLETED":
                    review = await db.scalar(
                        select(Review).where(
                            Review.data_request_id == request.id,
                            Review.review_type == "FINAL",
                        )
                    )
                    if review is None:
                        db.add(Review(
                            data_request_id=request.id,
                            stage_run_id=stage.id if stage is not None else None,
                            artifact_id=artifact.id,
                            reviewer_id=users["E2E-MANAGER"].id,
                            reviewer_name=users["E2E-MANAGER"].name,
                            review_type="FINAL",
                            decision=ReviewDecision.APPROVED.value,
                            feedback="E2E 최종 산출물 검토 완료",
                            created_at=now,
                        ))

            if run_status == "FAILED":
                existing_failure_event = await db.scalar(
                    select(PipelineEvent).where(
                        PipelineEvent.pipeline_run_id == run.id,
                        PipelineEvent.event_type == EventType.FAILED.value,
                    )
                )
                if existing_failure_event is None:
                    db.add(PipelineEvent(
                        pipeline_run_id=run.id,
                        stage_run_id=stage.id if stage is not None else None,
                        event_type=EventType.FAILED.value,
                        severity="HIGH",
                        message="E2E 파이프라인 실패",
                        payload={
                            "e2e_seed": True,
                            "failure_count": 3 if request_no == "E2E-FAIL-002" else 1,
                        },
                        occurred_at=now,
                    ))
        await db.commit()
    print("Seeded E2E users: " + ", ".join(code for code, *_ in USERS))
    print("Seeded E2E tasks: " + ", ".join(request_no for request_no, *_ in TASKS))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
