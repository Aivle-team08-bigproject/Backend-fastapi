"""Deterministic workflow records used by dashboard contract tests."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.core import security
from app.db.session import AsyncSessionLocal
from app.domains.dashboard.model import TaskViewSnapshot
from app.domains.employees.model import Employee, EmployeeStatus
from app.domains.pipeline.model import (
    Client,
    DataRequest,
    PipelineRun,
    Review,
    StageRun,
)


@dataclass
class DashboardFixture:
    """Persisted records belonging to one isolated dashboard scenario."""

    marker: str
    client: Client
    employee: Employee
    data_request: DataRequest
    pipeline_run: PipelineRun | None
    stage_runs: list[StageRun]
    review: Review | None
    snapshot: TaskViewSnapshot
    password: str

    @property
    def request_no(self) -> str:
        return self.data_request.request_no

    @property
    def view_code(self) -> str:
        return self.snapshot.view_code


class DashboardFixtureFactory:
    """Create unique workflow data without relying on demo dashboard metadata."""

    def __init__(self) -> None:
        self._counter = 0

    def _marker(self) -> str:
        self._counter += 1
        return f"{self._counter:03d}-{uuid4().hex[:8]}"

    def create(
        self,
        *,
        created_at: datetime | None = None,
        request_no: str | None = None,
        view_code: str | None = None,
        include_pipeline: bool = True,
        include_stage: bool = True,
        include_owner: bool = True,
        pipeline_status: str = "RUNNING",
        current_stage: str | None = "DATA_PROCESSING",
        stage_code: str = "DATA_PROCESSING",
        stage_status: str = "RUNNING",
        review_type: str | None = None,
        review_decision: str | None = None,
        analysis_condition: dict | None = None,
        snapshot_payload: dict | None = None,
    ) -> DashboardFixture:
        """Synchronously persist one complete or intentionally partial scenario."""

        return asyncio.run(
            self.acreate(
                created_at=created_at,
                request_no=request_no,
                view_code=view_code,
                include_pipeline=include_pipeline,
                include_stage=include_stage,
                include_owner=include_owner,
                pipeline_status=pipeline_status,
                current_stage=current_stage,
                stage_code=stage_code,
                stage_status=stage_status,
                review_type=review_type,
                review_decision=review_decision,
                analysis_condition=analysis_condition,
                snapshot_payload=snapshot_payload,
            )
        )

    async def acreate(
        self,
        *,
        created_at: datetime | None = None,
        request_no: str | None = None,
        view_code: str | None = None,
        include_pipeline: bool = True,
        include_stage: bool = True,
        include_owner: bool = True,
        pipeline_status: str = "RUNNING",
        current_stage: str | None = "DATA_PROCESSING",
        stage_code: str = "DATA_PROCESSING",
        stage_status: str = "RUNNING",
        review_type: str | None = None,
        review_decision: str | None = None,
        analysis_condition: dict | None = None,
        snapshot_payload: dict | None = None,
    ) -> DashboardFixture:
        """Async counterpart for tests that already own an event loop."""

        marker = self._marker()
        now = (created_at or datetime.now(timezone.utc)).replace(microsecond=0)
        request_no = f"{request_no}-{marker}" if request_no else f"REQ/{marker}"
        view_code = f"{view_code}-{marker}" if view_code else f"VIEW/{marker}"
        password = f"Dashboard!{marker}"

        customer = Client(
            company_name=f"Dashboard Client {marker}",
            business_registration_number=f"DASH-{marker}",
            contact_name=f"담당자 {marker}",
            status="ACTIVE",
            created_at=now,
            updated_at=now,
        )
        employee = Employee(
            employee_code=f"DASH-{marker}",
            name=f"대시보드 담당자 {marker}",
            department="데이터사업팀",
            password_hash=security.hash_password(password),
            status=EmployeeStatus.ACTIVE,
            must_change_password=False,
            failed_login_count=0,
            auth_version=1,
            created_by="SYSTEM",
            created_at=now,
            updated_at=now,
        )

        async with AsyncSessionLocal() as db:
            db.add_all([customer, employee])
            await db.flush()

            data_request = DataRequest(
                request_no=request_no,
                client_id=customer.id,
                owner_id=employee.id if include_owner else None,
                requester_name=f"요청자 {marker}",
                owner_name=employee.name if include_owner else None,
                title=f"대시보드 계약 작업 {marker}",
                business_purpose="dashboard contract test",
                raw_requirement=f"계약 테스트 요구사항 {marker}",
                output_formats=["CSV"],
                delivery_channels=["FILE_DOWNLOAD"],
                analysis_condition=analysis_condition or {},
                status="COMPLETED" if pipeline_status == "COMPLETED" else "RUNNING",
                created_at=now,
                updated_at=now + timedelta(minutes=1),
            )
            db.add(data_request)
            await db.flush()

            pipeline_run: PipelineRun | None = None
            stage_runs: list[StageRun] = []
            if include_pipeline:
                pipeline_run = PipelineRun(
                    data_request_id=data_request.id,
                    attempt_no=1,
                    status=pipeline_status,
                    current_stage=current_stage,
                    progress_percent=100 if pipeline_status == "COMPLETED" else 50,
                    started_at=now,
                    completed_at=now + timedelta(minutes=1) if pipeline_status == "COMPLETED" else None,
                    created_at=now,
                    updated_at=now + timedelta(minutes=1),
                )
                db.add(pipeline_run)
                await db.flush()

                if include_stage:
                    stage_run = StageRun(
                        pipeline_run_id=pipeline_run.id,
                        stage_code=stage_code,
                        attempt_no=1,
                        status=stage_status,
                        executor="TEST",
                        input_payload={"marker": marker},
                        output_payload={"marker": marker},
                        validation_result={},
                        started_at=now,
                        completed_at=now + timedelta(minutes=1) if stage_status == "COMPLETED" else None,
                        created_at=now,
                    )
                    db.add(stage_run)
                    stage_runs.append(stage_run)
                    await db.flush()

            review: Review | None = None
            if review_type is not None:
                review = Review(
                    data_request_id=data_request.id,
                    stage_run_id=stage_runs[0].id if stage_runs else None,
                    reviewer_id=employee.id,
                    reviewer_name=employee.name,
                    review_type=review_type,
                    decision=review_decision or "APPROVED",
                    feedback=f"fixture review {marker}",
                    created_at=now + timedelta(minutes=2),
                )
                db.add(review)

            payload = snapshot_payload or {
                "marker": marker,
                "request_no": request_no,
                "view_code": view_code,
                "rows": [{"source": "workflow", "value": marker}],
            }
            snapshot = TaskViewSnapshot(
                data_request_id=data_request.id,
                view_code=view_code,
                payload=payload,
                created_at=now,
                updated_at=now + timedelta(minutes=1),
            )
            db.add(snapshot)
            await db.commit()

        return DashboardFixture(
            marker=marker,
            client=customer,
            employee=employee,
            data_request=data_request,
            pipeline_run=pipeline_run,
            stage_runs=stage_runs,
            review=review,
            snapshot=snapshot,
            password=password,
        )
