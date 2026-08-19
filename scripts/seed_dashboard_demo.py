"""대시보드와 작업 상세 API용 데모 데이터를 멱등 적재한다."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from app.common.time_utils import utcnow
from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.core.security import generate_temporary_password, hash_password
from app.domains.dashboard.model import DashboardAlert, DashboardInsight, TaskViewSnapshot
from app.domains.employees.model import Department, Employee, EmployeePermission, EmployeeStatus, PermissionCode
from app.domains.pipeline.model import (
    AgentMetric,
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
from scripts.demo_requirements import build_demo_requirement


BASE_TASKS = [
    ("REQ-2024-0847", "(주)ABC마케팅", "소비 트렌드 분석", "가공 완료 - 배포 대기", "홍길동 책임", "요구사항 완료 피드백"),
    ("REQ-2024-0846", "하나은행 미래금융팀", "부동산 신용 대출 흐름", "데이터 이관 검토 중", "김민수 선임", "데이터 선별 진행"),
    ("REQ-2024-0845", "스타벅스 코리아", "상권 활성화 점수 산출", "GIS 지오코딩 작업 완료", "이지은 선임", "데이터 가공 진행"),
    ("REQ-2024-0844", "SK텔레콤 AI혁신본부", "유동인구 기반 매출 분석", "고객사 최종 승인 대기", "박준영 책임", "최종 산출물 및 피드백"),
    ("REQ-2024-0843", "올리브영 신상품파트", "화장품 구매 선호도 조사", "요구사항 정의서 작성 중", "홍길동 책임", "요구사항 분석"),
    ("REQ-2024-0842", "넷마블 신작기획팀", "모바일 게임 리텐션 분석", "데이터셋 매칭 진행 중", "김민수 선임", "데이터 선별 진행"),
    ("REQ-2024-0841", "대한항공 종합전략부", "항공편 수요 예측 통계", "집계/가공 단계 진행", "이지은 선임", "데이터 가공 진행"),
    ("REQ-2024-0840", "네이버 쇼핑전략팀", "온라인 소비 장바구니 매핑", "최종 산출물 배포 완료", "박준영 책임", "작업완료"),
    ("REQ-2024-0839", "현대카드 마케팅팀", "신용카드 소비패턴 분석", "요건 파싱 대기", "홍길동 책임", "요구사항 분석 진행"),
    ("REQ-2024-0838", "쿠팡 물류기획부", "물류센터 배송동선 최적화", "실현가능성 검증 중", "김민수 선임", "샘플데이터 및 피드백"),
    ("REQ-2024-0837", "CJ제일제당 브랜드전략팀", "식품 구매 트렌드 분석", "데이터 정제 진행 중", "이지은 선임", "데이터 가공 진행"),
    ("REQ-2024-0836", "롯데마트 상품기획팀", "유통채널별 매출 비교", "QA 검증 완료", "박준영 책임", "최종 산출물 및 피드백"),
]

EXTRA_TASKS = [
    ("REQ-2024-0835", "서울시 정책연구원", "생활인구 밀집도 분석", "분석 범위 확인 필요", "홍길동 책임", "요구사항 완료 피드백"),
    ("REQ-2024-0834", "배달플랫폼 데이터팀", "배달 상권 성장률 분석", "결측값 보정 진행", "이지은 선임", "데이터 가공 진행"),
    ("REQ-2024-0833", "게임산업협회", "게임 결제 이상치 분석", "익명화 처리 진행", "이지은 선임", "데이터 가공 진행"),
    ("REQ-2024-0832", "한국관광공사", "관광 소비 동선 분석", "형식 변환 진행", "박준영 책임", "데이터 가공 진행"),
    ("REQ-2024-0831", "지역상권진흥원", "소상공인 매출 변화", "재배포 조건 확인 필요", "홍길동 책임", "요구사항 분석 진행"),
    ("REQ-2024-0830", "카드상품기획팀", "연령별 소비지수", "품질 스코어 산정", "김민수 선임", "샘플데이터 및 피드백"),
    ("REQ-2024-0829", "모빌리티연구소", "시간대별 이동 소비 분석", "데이터 병합 진행", "이지은 선임", "데이터 가공 진행"),
    ("REQ-2024-0828", "프랜차이즈협회", "가맹점 성장지수", "이상치 제거 진행", "박준영 책임", "데이터 가공 진행"),
    ("REQ-2024-0827", "핀테크연구센터", "결제 인증 패턴 분석", "PII 마스킹 진행", "김민수 선임", "데이터 가공 진행"),
    ("REQ-2024-0826", "유통데이터랩", "월별 구매전환 분석", "산출물 전달 완료", "박준영 책임", "작업완료"),
    ("REQ-2024-0825", "공공데이터센터", "지역 소비 활성도", "API 발급 완료", "홍길동 책임", "작업완료"),
    ("REQ-2024-0812", "(주)AI산업혁신원", "의료 영상 분석 가공", "의료 영상 뼈 분할 라벨 가이드라인 누락", "홍길동 책임", "최종 산출물 및 피드백"),
]

TIMELINES = {
    "selection": {
        "timelineItems": [
            {"title": "데이터 소스 탐색", "time": "15:01:22", "description": "관련 데이터 소스 5개 식별 완료", "state": "done"},
            {"title": "샘플 데이터 추출", "time": "15:08:47", "description": "대표 샘플 200건 추출 및 정합성 검증", "state": "done"},
            {"title": "품질 스코어 산정", "time": "15:12:30", "description": "완결성 88.5%, 정확도 92.1% 측정", "state": "done"},
        ],
        "logLines": [
            {"time": "15:01:22", "agent": "Agent-Scanner", "agentColor": "#22c55e", "message": "5 data sources identified. Relevance score: 94.8%"},
            {"time": "15:08:47", "agent": "Agent-Selector", "agentColor": "#008485", "message": "Sample extraction complete. Integrity check: PASS"},
            {"time": "15:12:30", "agent": "Agent-QA", "agentColor": "#eab308", "message": "Quality scoring in progress... ETA 2 minutes"},
        ],
    },
    "processing": {
        "timelineItems": [
            {"title": "결측값 보정", "time": "16:05:10", "description": "NULL 값 432건 보정 및 이상치 클렌징 완료", "state": "done"},
            {"title": "형식 변환", "time": "16:18:33", "description": "CSV 포맷 변환 및 UTF-8 인코딩 적용", "state": "done"},
            {"title": "익명화 처리", "time": "16:25:00", "description": "PII 마스킹 12/24 청크 처리 중", "state": "done"},
        ],
        "logLines": [
            {"time": "16:05:10", "agent": "Agent-Cleaner", "agentColor": "#22c55e", "message": "Null imputation complete. Outlier cleansing applied."},
            {"time": "16:18:33", "agent": "Agent-Transformer", "agentColor": "#008485", "message": "CSV format validation pass. Header alignment OK."},
            {"time": "16:25:00", "agent": "Agent-Privacy", "agentColor": "#eab308", "message": "Progress: 50% — ETA 3 minutes remaining"},
        ],
    },
}

VIEW_PAYLOADS = {
    "register": {
        "placeholder": "예: 강남구 외식업 소비 트렌드를 2024년 1~6월 기간으로 분석하고 싶습니다.",
    },
    "analysis": {
        "timelineItems": [
            {"title": "요건 파싱", "time": "14:23:05", "description": "자연어 요청을 정형 필드로 구조화 완료", "state": "done"},
            {"title": "데이터셋 매칭", "time": "14:23:12", "description": "내부 데이터 매핑률 94.2% 검증", "state": "done"},
            {"title": "실현가능성 검증", "time": "14:23:45", "description": "가용 데이터와 비식별 조건 검증 완료", "state": "done"},
        ],
        "logLines": [
            {"time": "14:23:05", "agent": "Agent-Parser", "agentColor": "#22c55e", "message": "Entities matching schema mapping completed."},
            {"time": "14:23:30", "agent": "Agent-Matcher", "agentColor": "#008485", "message": "Dataset match-rate checks: 94.2% suitability."},
            {"time": "14:23:55", "agent": "Agent-Validator", "agentColor": "#22c55e", "message": "Compliance verified. Privacy check cleared."},
        ],
    },
    "review": {
        "usagePurpose": "강남구 외식업 소비 트렌드 분석",
        "dataDescription": "2024년 1~6월 강남구 외식업 매장별 결제 데이터 및 소비 패턴",
        "columns": [
            {"name": "store_id", "type": "VARCHAR", "description": "매장 고유 ID"},
            {"name": "transaction_date", "type": "DATETIME", "description": "결제 일시"},
            {"name": "amount", "type": "INTEGER", "description": "결제 금액"},
            {"name": "category", "type": "VARCHAR", "description": "업종 분류"},
            {"name": "age_group", "type": "VARCHAR", "description": "연령대"},
        ],
        "estimatedCount": "약 254,320건",
        "deliveryMedium": "API",
        "outputFormat": "CSV",
        "feedbackPlaceholder": "수정이 필요한 사항을 자유롭게 입력해주세요.",
    },
    "selection": TIMELINES["selection"],
    "sample-feedback": {
        "sampleRows": [
            {"district": "강남구", "neighborhood": dong, "category": category, "ageGroup": age, "paymentMonth": month, "salesIndex": score}
            for dong, category, age, month, score in [
                ("역삼동", "한식", "30대", "2024-10", "142.5"),
                ("신사동", "일식", "20대", "2024-10", "156.8"),
                ("청담동", "양식", "40대", "2024-10", "128.4"),
                ("논현동", "카페", "30대", "2024-10", "165.2"),
                ("삼성동", "중식", "20대", "2024-10", "119.7"),
            ]
        ],
        "columnInfo": [
            {"title": "지역(구) / 지역(동)", "description": "서울시 행정구역 기준의 상권 분석 공간 단위입니다."},
            {"title": "업종", "description": "가맹점 MCC 업종 분류를 분석용 대분류로 변환합니다."},
            {"title": "연령대", "description": "카드 소지자 연령을 10세 단위로 비식별 집계합니다."},
            {"title": "결제월", "description": "승인 일자를 YYYY-MM 단위로 집계합니다."},
            {"title": "매출지수", "description": "전체 평균 100 기준의 상대 매출 강도입니다."},
        ],
        "feedbackPlaceholder": "특이치(Outlier) 제거 로직을 강화해주세요.",
    },
    "processing": TIMELINES["processing"],
    "final-feedback": {
        "outputRows": [
            {"district": "강남구", "neighborhood": dong, "category": category, "ageGroup": age, "paymentMonth": month, "salesIndex": score}
            for dong, category, age, month, score in [
                ("역삼동", "한식", "30대", "2024-01", "142.5"),
                ("신사동", "일식", "20대", "2024-02", "156.8"),
                ("청담동", "양식", "40대", "2024-03", "128.4"),
                ("논현동", "카페", "30대", "2024-04", "165.2"),
                ("삼성동", "중식", "20대", "2024-05", "119.7"),
            ]
        ],
        "reportTitle": "강남구 외식업 소비 트렌드 분석 보고서",
        "reportMeta": "Lumen AI Generated Report • 2024년 12월",
        "insightSummary": ["1. 역삼동 및 신사동 소비 지수가 전월 대비 상승했습니다.", "2. 30대 결제 비중이 가장 높게 나타났습니다."],
        "chartBars": [
            {"label": "한식", "height": 48, "highlight": False},
            {"label": "일식", "height": 78, "highlight": True},
            {"label": "양식", "height": 60, "highlight": False},
            {"label": "카페", "height": 102, "highlight": True},
            {"label": "중식", "height": 36, "highlight": False},
        ],
        "infoRows": [
            {"label": "산출물 형식", "value": "CSV + 보고서"},
            {"label": "데이터 건수", "value": "254,320건"},
            {"label": "전달 매체", "value": "API"},
            {"label": "생성 일시", "value": "2024-12-15 16:30"},
        ],
        "feedbackPlaceholder": "산출물에 대한 수정 사항을 자유롭게 입력해주세요.",
    },
    "complete": {
        "milestones": [
            {"title": "요구사항 분석", "time": "14:23", "description": "완료, 매칭률 94.2%"},
            {"title": "데이터 선별", "time": "14:58", "description": "완료, 254,320건 추출"},
            {"title": "데이터 가공", "time": "16:57", "description": "완료, 품질 98.7점"},
            {"title": "QA 검증", "time": "17:02", "description": "완료, 합격"},
        ],
        "files": [{"name": "Final_Dataset.csv", "size": "42.8MB", "kind": "csv"}, {"name": "Final_Dataset.xlsx", "size": "51.2MB", "kind": "xlsx"}],
        "endpointUrl": "https://api.example.com/v1/outputs/847",
        "apiKeyMasked": "••••••••••••••••",
        "recipientEmail": "recipient@customer.com",
        "emailSubject": "[Lumen Platform] 데이터 가공 산출물 송부",
    },
}

DEMO_EMPLOYEES = [
    ("DEMO-KIM-001", "김민수", "데이터 가공 파트", EmployeeStatus.ACTIVE, {PermissionCode.EMPLOYEE_UPDATE, PermissionCode.DATA_PRODUCT_WRITE}),
    ("DEMO-LEE-001", "이지은", "요구사항 분석 파트", EmployeeStatus.ACTIVE, {PermissionCode.DATA_PRODUCT_WRITE}),
    ("DEMO-PARK-001", "박준영", "데이터 선별 파트", EmployeeStatus.ACTIVE, {PermissionCode.DATA_PRODUCT_READ}),
    ("DEMO-CHOI-001", "최수민", "외부 협력 파트", EmployeeStatus.DISABLED, {PermissionCode.DATA_PRODUCT_READ}),
    ("DEMO-OPS-001", "정하늘", "플랫폼 운영 파트", EmployeeStatus.ACTIVE, {PermissionCode.DATA_PRODUCT_READ}),
    ("DEMO-QA-001", "윤서진", "품질 검증 파트", EmployeeStatus.ACTIVE, {PermissionCode.DATA_PRODUCT_READ}),
]

ASSIGNEE_EMPLOYEE_CODES = {
    "홍길동 책임": "DEMO-001",
    "김민수 선임": "DEMO-KIM-001",
    "이지은 선임": "DEMO-LEE-001",
    "박준영 책임": "DEMO-PARK-001",
}


def status_for(label: str) -> tuple[DataRequestStatus, str]:
    return {
        "요구사항 분석": (DataRequestStatus.WAITING_REVIEW, "REQUIREMENT_ANALYSIS"),
        "요구사항 분석 진행": (DataRequestStatus.RUNNING, "REQUIREMENT_ANALYSIS"),
        "요구사항 완료 피드백": (DataRequestStatus.WAITING_REVIEW, "REQUIREMENT_REVIEW"),
        "데이터 선별 진행": (DataRequestStatus.RUNNING, "DATA_SELECTION"),
        "샘플데이터 및 피드백": (DataRequestStatus.WAITING_REVIEW, "SAMPLE_FEEDBACK"),
        "데이터 가공 진행": (DataRequestStatus.RUNNING, "DATA_PROCESSING"),
        "최종 산출물 및 피드백": (DataRequestStatus.WAITING_REVIEW, "FINAL_FEEDBACK"),
        "작업완료": (DataRequestStatus.COMPLETED, "COMPLETED"),
    }[label]


async def seed_developer_monitoring_data(session, data_request: DataRequest, now: datetime) -> int:
    pipeline_run = await session.scalar(
        select(PipelineRun).where(
            PipelineRun.data_request_id == data_request.id,
            PipelineRun.attempt_no == 1,
        )
    )
    if pipeline_run is None:
        pipeline_run = PipelineRun(
            data_request_id=data_request.id,
            attempt_no=1,
            status=PipelineRunStatus.COMPLETED,
            current_stage="COMPLETED",
            progress_percent=100,
            started_at=now - timedelta(days=2),
            completed_at=now - timedelta(days=2) + timedelta(hours=1),
            created_at=now - timedelta(days=2),
            updated_at=now - timedelta(days=2) + timedelta(hours=1),
        )
        session.add(pipeline_run)
        await session.flush()

    stage_specs = {
        "REQUIREMENT_ANALYSIS": "requirement-analysis-agent",
        "DATA_SELECTION": "data-selection-agent",
        "DATA_PROCESSING": "data-processing-agent",
        "DELIVERY": "delivery-pipeline",
    }
    stage_runs: dict[str, StageRun] = {}
    for stage_code in stage_specs:
        stage_run = await session.scalar(
            select(StageRun).where(
                StageRun.pipeline_run_id == pipeline_run.id,
                StageRun.stage_code == stage_code,
                StageRun.attempt_no == 1,
            )
        )
        if stage_run is None:
            stage_run = StageRun(
                pipeline_run_id=pipeline_run.id,
                stage_code=stage_code,
                attempt_no=1,
                status=StageRunStatus.COMPLETED,
                model_name="dashboard-demo-v1",
                started_at=now - timedelta(days=2),
                completed_at=now - timedelta(days=2) + timedelta(hours=1),
                created_at=now - timedelta(days=2),
            )
            session.add(stage_run)
            await session.flush()
        stage_runs[stage_code] = stage_run

    await session.execute(delete(AgentMetric).where(AgentMetric.model_name == "dashboard-demo-v1"))
    await session.execute(delete(PipelineEvent).where(PipelineEvent.message.like("[DEMO]%")))

    local_now = now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(settings.dashboard_timezone))
    local_today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today = local_today.astimezone(timezone.utc)
    month_start = local_today.replace(day=1).astimezone(timezone.utc)
    agent_specs = [
        ("requirement-analysis-agent", "REQUIREMENT_ANALYSIS", 4200, 900, 110, 17),
        ("data-selection-agent", "DATA_SELECTION", 5600, 1200, 820, 9),
        ("data-processing-agent", "DATA_PROCESSING", 7800, 2100, 1450, 6),
        ("delivery-pipeline", "DELIVERY", 900, 180, 260, 23),
    ]
    metric_count = 0
    day_count = (today - month_start).days + 1
    for day_index in range(day_count):
        day = month_start + timedelta(days=day_index)
        for agent_index, (
            agent_name,
            stage_code,
            input_base,
            output_base,
            latency_base,
            failure_every,
        ) in enumerate(agent_specs):
            for call_index, hour in enumerate((2, 10, 18)):
                created_at = day + timedelta(hours=hour, minutes=agent_index * 7 + call_index)
                if created_at >= now:
                    continue
                outcome = (
                    "FAILED"
                    if (day_index * 3 + call_index + agent_index + 1) % failure_every == 0
                    else "SUCCEEDED"
                )
                input_tokens = input_base + day_index * 37 + call_index * 113
                output_tokens = output_base + day_index * 19 + call_index * 47
                total_tokens = input_tokens + output_tokens
                session.add(
                    AgentMetric(
                        stage_run_id=stage_runs[stage_code].id,
                        agent_name=agent_name,
                        model_name="dashboard-demo-v1",
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cost_usd=(Decimal(total_tokens) * Decimal("0.0000075")).quantize(Decimal("0.000001")),
                        latency_ms=latency_base + call_index * 75,
                        outcome=outcome,
                        created_at=created_at,
                    )
                )
                metric_count += 1

    latest_metrics = [
        ("requirement-analysis-agent", "REQUIREMENT_ANALYSIS", 4800, 980, 118, "SUCCEEDED", 3),
        ("data-selection-agent", "DATA_SELECTION", 6300, 1310, 2400, "SUCCEEDED", 2),
        ("data-processing-agent", "DATA_PROCESSING", 8100, 2250, None, "FAILED", 1),
    ]
    for agent_name, stage_code, input_tokens, output_tokens, latency_ms, outcome, minutes_ago in latest_metrics:
        total_tokens = input_tokens + output_tokens
        session.add(
            AgentMetric(
                stage_run_id=stage_runs[stage_code].id,
                agent_name=agent_name,
                model_name="dashboard-demo-v1",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=(Decimal(total_tokens) * Decimal("0.0000075")).quantize(Decimal("0.000001")),
                latency_ms=latency_ms,
                outcome=outcome,
                created_at=now - timedelta(minutes=minutes_ago),
            )
        )
        metric_count += 1

    demo_errors = [
        (
            "DATA_PROCESSING",
            "data-processing-agent",
            "[DEMO] 머징 결측치 비율 임계치 초과 (12.4%)",
            "HIGH",
            8,
        ),
        (
            "DATA_SELECTION",
            "data-selection-agent",
            "[DEMO] 데이터 카탈로그 조회 응답 시간이 제한을 초과했습니다.",
            "MEDIUM",
            37,
        ),
        (
            "DELIVERY",
            "delivery-pipeline",
            "[DEMO] 전달 API 인증 토큰 갱신이 지연되었습니다.",
            "LOW",
            83,
        ),
    ]
    for stage_code, agent_name, message, severity, minutes_ago in demo_errors:
        session.add(
            PipelineEvent(
                pipeline_run_id=pipeline_run.id,
                stage_run_id=stage_runs[stage_code].id,
                event_type=EventType.FAILED,
                severity=severity,
                message=message,
                payload={"agent_name": agent_name, "seed_code": "DEVELOPER_DASHBOARD"},
                occurred_at=now - timedelta(minutes=minutes_ago),
            )
        )
    return metric_count


async def _ensure_department(session, name: str, now) -> int:
    """이름으로 부서를 찾고, 없으면 만들어서 id를 반환한다 (데모 시드 전용 get-or-create)."""
    department_id = await session.scalar(select(Department.id).where(Department.name == name))
    if department_id is not None:
        return department_id
    department = Department(name=name, code=f"DEPT-{name}", is_active=True, created_at=now, updated_at=now)
    session.add(department)
    await session.flush()
    return department.id


async def upsert_dashboard_data() -> int:
    async with AsyncSessionLocal() as session:
        now = utcnow()
        for employee_code, name, department, employee_status, permissions in DEMO_EMPLOYEES:
            employee = await session.scalar(select(Employee).where(Employee.employee_code == employee_code))
            if employee is None:
                department_id = await _ensure_department(session, department, now)
                employee = Employee(
                    employee_code=employee_code,
                    name=name,
                    email=f"{employee_code.lower()}@company.com",
                    department_id=department_id,
                    password_hash=hash_password(generate_temporary_password()),
                    status=employee_status,
                    must_change_password=True,
                    failed_login_count=0,
                    locked_until=None,
                    auth_version=1,
                    created_by="DASHBOARD_DEMO_SEED",
                    created_at=now,
                    updated_at=now,
                )
                employee.permissions = [EmployeePermission(permission_code=permission) for permission in permissions]
                session.add(employee)

        all_tasks = BASE_TASKS + EXTRA_TASKS
        base_date = datetime(2024, 11, 12, 9, 0)
        monitoring_request = None
        for index, (request_no, company, data_type, detail, assignee, label) in enumerate(all_tasks):
            client = await session.scalar(select(Client).where(Client.company_name == company))
            if client is None:
                client = Client(company_name=company, created_at=now, updated_at=now)
                session.add(client)
                await session.flush()

            request = await session.scalar(select(DataRequest).where(DataRequest.request_no == request_no))
            db_status, current_stage = status_for(label)
            metadata = {
                "demo_dashboard": True,
                "data_type": data_type,
                "detail": detail,
                "assignee": assignee,
                "assignee_employee_code": ASSIGNEE_EMPLOYEE_CODES[assignee],
                "dashboard_status": label,
            }
            if request_no in {"REQ-2024-0847", "REQ-2024-0812"}:
                metadata["alert_code"] = "REQUIREMENT_GUIDE"
            created_at = base_date - timedelta(days=index)
            if request is None:
                request = DataRequest(
                    request_no=request_no,
                    client_id=client.id,
                    requester_name=company,
                    title=f"{company} 데이터 가공 요청의 건",
                    business_purpose=data_type,
                    raw_requirement=build_demo_requirement(f"{company} 데이터 가공 요청", data_type),
                    output_formats=["CSV"],
                    delivery_channels=["API"],
                    analysis_condition=metadata,
                    status=db_status,
                    created_at=created_at,
                    updated_at=created_at + timedelta(days=min(index % 3, 1)),
                )
                session.add(request)
                await session.flush()
            else:
                request.client_id = client.id
                request.title = f"{company} 데이터 가공 요청의 건"
                request.business_purpose = data_type
                request.raw_requirement = build_demo_requirement(f"{company} 데이터 가공 요청", data_type)
                request.analysis_condition = metadata
                request.status = db_status
                request.created_at = created_at
                request.updated_at = created_at + timedelta(days=min(index % 3, 1))

            if request_no == "REQ-2024-0847":
                monitoring_request = request

            for view_code, payload in VIEW_PAYLOADS.items():
                snapshot = await session.scalar(
                    select(TaskViewSnapshot).where(
                        TaskViewSnapshot.data_request_id == request.id,
                        TaskViewSnapshot.view_code == view_code,
                    )
                )
                if snapshot is None:
                    session.add(
                        TaskViewSnapshot(
                            data_request_id=request.id,
                            view_code=view_code,
                            payload=payload,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                else:
                    snapshot.payload = payload
                    snapshot.updated_at = now

        alert_rows = [
            ("REQUIREMENT_GUIDE", "요구사항 가이드 미확정", "2건", "#fde8e8", "#dc2626", "ABC마케팅 가이드 미달성 오류 피드백 지연", "대기 3일 경과", "/tasks/review?requestNo=REQ-2024-0847"),
            ("FORMAT_MISMATCH", "데이터 포맷 불일치 오류", "1건", "#fef3c7", "#d97706", "스타벅스 코리아 위치 데이터 위경도 누락건", "조치 필요", "/tasks/sample-feedback?requestNo=REQ-2024-0845"),
            ("QUALITY_THRESHOLD", "가공 품질 신뢰도 임계치 미달", "3건", "#fef3c7", "#d97706", "넷마블 게임데이터 머징 결측치 발생률 12% 초과", "재작업 권장", "/tasks/final-feedback?requestNo=REQ-2024-0842"),
        ]
        for order, row in enumerate(alert_rows):
            alert = await session.scalar(select(DashboardAlert).where(DashboardAlert.alert_code == row[0]))
            values = dict(
                alert_code=row[0], title=row[1], count_label=row[2], count_bg=row[3], count_color=row[4],
                description=row[5], foot_note=row[6], action_to=row[7], display_order=order,
                updated_at=now,
            )
            if alert is None:
                session.add(DashboardAlert(**values))
            else:
                for key, value in values.items():
                    setattr(alert, key, value)

        insight_rows = [
            ("PREFERRED", 1, "2030대 소비 구매 인덱스", "카드 결제", None, None, "인기", "#dcfce7", "#22c55e"),
            ("PREFERRED", 2, "전국 스타벅스 상권 유동인구", "위치정보", None, None, "상승", "#e6f0ff", "#0066ff"),
            ("PREFERRED", 3, "모바일 게임 주간 리텐션 통계", "가공데이터", None, None, "인기", "#dcfce7", "#22c55e"),
            ("PREFERRED", 4, "수도권 아파트 대출 신용 평가 데이터", "금융 통계", None, None, "유지", "#e6f0ff", "#0066ff"),
            ("SUPPLEMENT", 1, "골프장 법인카드 정밀 소비 데이터", None, "상세 분석 가이드 부재", "#d97706", "가이드 보완 필요", "#fde8e8", "#dc2626"),
            ("SUPPLEMENT", 2, "전국 공항 항공편 지연 시간 예측 세트", None, "최신성 결여 (2024년 6월 이후 중단)", "#d97706", "업데이트 필요", "#fde8e8", "#dc2626"),
            ("SUPPLEMENT", 3, "온라인 유통 장바구니 카테고리 매핑", None, "소분류 정확도 저하 (임계치 85% 미만)", "#d97706", "품질 보완 필요", "#fde8e8", "#dc2626"),
        ]
        for row in insight_rows:
            insight = await session.scalar(
                select(DashboardInsight).where(DashboardInsight.section == row[0], DashboardInsight.display_order == row[1])
            )
            values = dict(section=row[0], display_order=row[1], title=row[2], subtitle=row[3], note=row[4], note_color=row[5], tag=row[6], tag_bg=row[7], tag_color=row[8])
            if insight is None:
                session.add(DashboardInsight(**values))
            else:
                for key, value in values.items():
                    setattr(insight, key, value)

        if monitoring_request is None:
            raise RuntimeError("개발자 대시보드용 기준 요청을 찾을 수 없습니다.")
        metric_count = await seed_developer_monitoring_data(session, monitoring_request, now)

        await session.commit()
        return metric_count


async def main() -> None:
    try:
        metric_count = await upsert_dashboard_data()
        print("dashboard_demo_tasks=24")
        print(f"task_view_snapshots={24 * len(VIEW_PAYLOADS)}")
        print(f"developer_dashboard_metrics={metric_count}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
