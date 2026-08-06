"""whddhs6645@company.com 계정 앞으로 우선순위 큐·캘린더 확인용 더미 작업 10개를 멱등 적재한다.

실제 파이프라인(Celery/에이전트)은 태우지 않는다 — DataRequest/PipelineRun/StageRun을
대시보드 프로젝션 쿼리(app/domains/dashboard/service.py의 _projection_query)가 기대하는
조합으로 직접 만든다. priority_code는 PipelineRun.status가 WAITING_*_REVIEW이고
PipelineRun+StageRun이 둘 다 있어야 계산되므로 반드시 두 테이블을 같이 만든다.
"""

import asyncio
from datetime import timedelta

from sqlalchemy import select

from app.common.time_utils import utcnow
from app.db.session import AsyncSessionLocal, engine
from app.domains.employees.model import Employee
from app.domains.pipeline.model import (
    Client,
    DataRequest,
    DataRequestStatus,
    PipelineRun,
    PipelineRunStatus,
    StageRun,
    StageRunStatus,
)

OWNER_EMAIL = "whddhs6645@company.com"

# (request_no suffix, client, title, run_status, current_stage, stage_status, due_in_days)
TASKS = [
    ("DEMO-01", "동그라미커머스", "온라인몰 재구매 고객 세그먼트 분석", PipelineRunStatus.QUEUED, "REQUIREMENT_ANALYSIS", StageRunStatus.PENDING, 7),
    ("DEMO-02", "세모물류", "권역별 배송 리드타임 분석", PipelineRunStatus.RUNNING, "REQUIREMENT_ANALYSIS", StageRunStatus.RUNNING, 6),
    ("DEMO-03", "네모식품", "즉석식품 카테고리 소비 트렌드", PipelineRunStatus.WAITING_REQUIREMENT_REVIEW, "REQUIREMENT_ANALYSIS", StageRunStatus.COMPLETED, 1),
    ("DEMO-04", "별표금융", "카드 소비 이상거래 패턴 분석", PipelineRunStatus.WAITING_REQUIREMENT_REVIEW, "REQUIREMENT_ANALYSIS", StageRunStatus.COMPLETED, 4),
    ("DEMO-05", "하트뷰티", "화장품 재구매 주기 분석", PipelineRunStatus.RUNNING, "DATA_SELECTION", StageRunStatus.RUNNING, 5),
    ("DEMO-06", "다이아렌탈", "차량 렌탈 이용 패턴 분석", PipelineRunStatus.WAITING_SAMPLE_REVIEW, "DATA_SELECTION", StageRunStatus.COMPLETED, 2),
    ("DEMO-07", "클로버여행", "여행 상품 시즌별 예약 분석", PipelineRunStatus.RUNNING, "DATA_PROCESSING", StageRunStatus.RUNNING, 8),
    ("DEMO-08", "스퀘어교육", "온라인 강좌 수강 완주율 분석", PipelineRunStatus.WAITING_FINAL_REVIEW, "DATA_PROCESSING", StageRunStatus.COMPLETED, -1),
    ("DEMO-09", "펜타건설", "지역별 부동산 거래 동향 분석", PipelineRunStatus.FAILED, "DATA_SELECTION", StageRunStatus.FAILED, 3),
    ("DEMO-10", "옥타곤제약", "약국 처방 데이터 소비 패턴", PipelineRunStatus.COMPLETED, "DATA_PROCESSING", StageRunStatus.COMPLETED, -5),
]


async def upsert_demo_tasks() -> int:
    async with AsyncSessionLocal() as db:
        owner = await db.scalar(select(Employee).where(Employee.email == OWNER_EMAIL))
        if owner is None:
            raise RuntimeError(f"소유자 계정을 찾을 수 없습니다: {OWNER_EMAIL}")

        now = utcnow()
        created_count = 0
        for index, (suffix, company, title, run_status, current_stage, stage_status, due_in_days) in enumerate(TASKS):
            request_no = f"REQ-DEMO-{suffix}"

            client = await db.scalar(select(Client).where(Client.company_name == company))
            if client is None:
                client = Client(company_name=company, created_at=now, updated_at=now)
                db.add(client)
                await db.flush()

            due_at = (now + timedelta(days=due_in_days)).replace(microsecond=0)
            analysis_condition = {"demo_priority_calendar": True, "due_at": due_at.isoformat()}
            request_status = (
                DataRequestStatus.COMPLETED
                if run_status == PipelineRunStatus.COMPLETED
                else DataRequestStatus.FAILED
                if run_status == PipelineRunStatus.FAILED
                else DataRequestStatus.WAITING_REVIEW
                if run_status
                in (
                    PipelineRunStatus.WAITING_REQUIREMENT_REVIEW,
                    PipelineRunStatus.WAITING_SAMPLE_REVIEW,
                    PipelineRunStatus.WAITING_FINAL_REVIEW,
                )
                else DataRequestStatus.RUNNING
                if run_status == PipelineRunStatus.RUNNING
                else DataRequestStatus.QUEUED
            )
            created_at = now - timedelta(days=index)

            request = await db.scalar(select(DataRequest).where(DataRequest.request_no == request_no))
            if request is None:
                request = DataRequest(
                    request_no=request_no,
                    client_id=client.id,
                    owner_id=owner.id,
                    owner_name=owner.name,
                    requester_name=company,
                    title=title,
                    raw_requirement=f"{title} 관련 데이터 가공을 요청합니다.",
                    output_formats=["CSV", "XLSX"],
                    delivery_channels=["FILE_DOWNLOAD"],
                    analysis_condition=analysis_condition,
                    status=request_status,
                    created_at=created_at,
                    updated_at=created_at,
                )
                db.add(request)
                await db.flush()
                created_count += 1
            else:
                request.client_id = client.id
                request.owner_id = owner.id
                request.owner_name = owner.name
                request.title = title
                request.analysis_condition = analysis_condition
                request.status = request_status
                request.updated_at = now

            run = await db.scalar(
                select(PipelineRun).where(PipelineRun.data_request_id == request.id, PipelineRun.attempt_no == 1)
            )
            if run is None:
                run = PipelineRun(
                    data_request_id=request.id,
                    attempt_no=1,
                    status=run_status,
                    current_stage=current_stage,
                    progress_percent=100 if run_status in (PipelineRunStatus.COMPLETED, PipelineRunStatus.FAILED) else 40,
                    created_at=created_at,
                    updated_at=now,
                    completed_at=now if run_status in (PipelineRunStatus.COMPLETED, PipelineRunStatus.FAILED) else None,
                )
                db.add(run)
                await db.flush()
            else:
                run.status = run_status
                run.current_stage = current_stage
                run.updated_at = now

            stage = await db.scalar(
                select(StageRun).where(
                    StageRun.pipeline_run_id == run.id,
                    StageRun.stage_code == current_stage,
                    StageRun.attempt_no == 1,
                )
            )
            if stage is None:
                db.add(
                    StageRun(
                        pipeline_run_id=run.id,
                        stage_code=current_stage,
                        attempt_no=1,
                        status=stage_status,
                        executor="CELERY",
                        input_payload={"request_no": request_no},
                        output_payload={},
                        created_at=created_at,
                        started_at=created_at,
                        completed_at=now if stage_status in (StageRunStatus.COMPLETED, StageRunStatus.FAILED) else None,
                    )
                )
            else:
                stage.status = stage_status

        await db.commit()
        return created_count


async def main() -> None:
    try:
        created = await upsert_demo_tasks()
        print(f"owner={OWNER_EMAIL}")
        print(f"tasks_total={len(TASKS)} newly_created={created}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
