from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import not_found
from app.common.time_utils import as_utc, utcnow
from app.core.config import settings
from app.domains.dashboard.model import DashboardAlert, DashboardInsight, TaskViewSnapshot
from app.domains.dashboard.schema import (
    AgentFailureRateResponse,
    AgentStatusResponse,
    DeveloperDashboardPayload,
    DeveloperDashboardPeriod,
    PractitionerDashboardResponse,
    DeveloperDashboardResponse,
    DeveloperErrorLogResponse,
    MemberManagementResponse,
    MemberResponse,
    MyTaskStatusResponse,
    PreferredItemResponse,
    StatCardResponse,
    SupplementItemResponse,
    TaskLookupResponse,
    TaskRowResponse,
    TaskViewResponse,
    TokenUsagePointResponse,
    TokenUsageSummaryResponse,
    WarningCardResponse,
)
from app.domains.pipeline.model import Client, DataRequest
from app.domains.pipeline.model import AgentMetric, EventType, PipelineEvent, StageRun
from app.domains.employees.model import Employee, EmployeeStatus, PermissionCode


AGENT_LABELS = {
    "requirement-analysis-agent": "요구사항 분석 에이전트",
    "data-selection-agent": "데이터 선별 에이전트",
    "data-processing-agent": "데이터 가공 에이전트",
    "delivery-pipeline": "배포 파이프라인",
}
AGENT_CARD_ORDER = (
    "requirement-analysis-agent",
    "data-selection-agent",
    "data-processing-agent",
)
FAILURE_RATE_ORDER = (*AGENT_CARD_ORDER, "delivery-pipeline")
DELAY_THRESHOLD_MS = 2_000


def _to_dashboard_time(value: datetime) -> datetime:
    return as_utc(value).astimezone(ZoneInfo(settings.dashboard_timezone))


def _to_utc(value: datetime) -> datetime:
    """대시보드의 모든 시간 비교를 UTC aware datetime으로 통일한다."""
    return as_utc(value)


def _task_row(data_request: DataRequest, client: Client) -> TaskRowResponse:
    metadata = data_request.analysis_condition or {}
    return TaskRowResponse(
        request_no=data_request.request_no,
        client=client.company_name,
        data_type=metadata.get("data_type", "데이터 분석"),
        detail=metadata.get("detail", data_request.title),
        assignee=metadata.get("assignee", "미배정"),
        created_at=data_request.created_at.strftime("%Y.%m.%d"),
        updated_at=data_request.updated_at.strftime("%Y.%m.%d"),
        status=metadata.get("dashboard_status", "요구사항 분석"),
    )


async def _demo_tasks(db: AsyncSession) -> list[tuple[DataRequest, Client]]:
    rows = (
        await db.execute(
            select(DataRequest, Client)
            .join(Client, Client.id == DataRequest.client_id)
            .order_by(DataRequest.created_at.desc(), DataRequest.request_no.desc())
        )
    ).all()
    return [(request, client) for request, client in rows if (request.analysis_condition or {}).get("demo_dashboard")]


async def get_practitioner_dashboard(db: AsyncSession) -> PractitionerDashboardResponse:
    task_pairs = await _demo_tasks(db)
    task_rows = [_task_row(request, client) for request, client in task_pairs]
    counts = {"요구사항 분석": 0, "진행중": 0, "가공중": 0, "완료": 0}
    for row in task_rows:
        counts[row.status] = counts.get(row.status, 0) + 1

    alerts = list((await db.scalars(select(DashboardAlert).order_by(DashboardAlert.display_order))).all())
    insights = list(
        (await db.scalars(select(DashboardInsight).order_by(DashboardInsight.section, DashboardInsight.display_order))).all()
    )
    preferred = [item for item in insights if item.section == "PREFERRED"]
    supplement = [item for item in insights if item.section == "SUPPLEMENT"]

    return PractitionerDashboardResponse(
        stat_cards=[
            StatCardResponse(
                label="전체 활성 작업",
                value=len(task_rows),
                unit="건",
                caption=f"대기 {counts['요구사항 분석']} / 진행 {counts['진행중'] + counts['가공중']} / 완료 {counts['완료']}",
                highlight=True,
            ),
            StatCardResponse(label="요구사항 분석 단계", value=counts["요구사항 분석"], unit="건", caption="평균 소요 1.2일"),
            StatCardResponse(label="데이터 선별 단계", value=counts["진행중"], unit="건", caption="평균 소요 2.4일"),
            StatCardResponse(label="데이터 가공 단계", value=counts["가공중"], unit="건", caption="평균 소요 3.5일"),
        ],
        alert_banner_count=sum(int(alert.count_label.removesuffix("건")) for alert in alerts),
        warning_cards=[
            WarningCardResponse(
                title=alert.title,
                count_label=alert.count_label,
                count_bg=alert.count_bg,
                count_color=alert.count_color,
                description=alert.description,
                foot_note=alert.foot_note,
                action_to=alert.action_to,
            )
            for alert in alerts
        ],
        preferred_items=[
            PreferredItemResponse(
                rank=item.display_order,
                title=item.title,
                subtitle=item.subtitle or "",
                tag=item.tag,
                tag_bg=item.tag_bg,
                tag_color=item.tag_color,
            )
            for item in preferred
        ],
        supplement_items=[
            SupplementItemResponse(
                title=item.title,
                note=item.note or "",
                note_color=item.note_color or "#d97706",
                tag=item.tag,
                tag_bg=item.tag_bg,
                tag_color=item.tag_color,
            )
            for item in supplement
        ],
        task_rows=task_rows,
        page_size=4,
    )


async def get_my_task_status(db: AsyncSession, employee: Employee) -> MyTaskStatusResponse:
    task_pairs = await _demo_tasks(db)
    assigned_tasks = []
    for request, client in task_pairs:
        metadata = request.analysis_condition or {}
        is_assigned = metadata.get("assignee_employee_code") == employee.employee_code
        if not is_assigned and not metadata.get("assignee_employee_code"):
            is_assigned = metadata.get("assignee") == employee.name
        if is_assigned:
            assigned_tasks.append(_task_row(request, client))

    active_tasks = [task for task in assigned_tasks if task.status != "완료"]
    completed_count = sum(task.status == "완료" for task in assigned_tasks)
    completion_rate = round(completed_count / len(assigned_tasks) * 100, 1) if assigned_tasks else 0.0
    return MyTaskStatusResponse(
        employee_code=employee.employee_code,
        user_name=employee.name,
        department=employee.department,
        active_count=len(active_tasks),
        urgent_count=sum(task.status == "요구사항 분석" for task in active_tasks),
        completed_count=completed_count,
        completion_rate=completion_rate,
        tasks=assigned_tasks,
    )


async def get_task_lookup(db: AsyncSession) -> TaskLookupResponse:
    pairs = await _demo_tasks(db)
    selected = [
        _task_row(request, client)
        for request, client in pairs
        if (request.analysis_condition or {}).get("alert_code") == "REQUIREMENT_GUIDE"
    ]
    return TaskLookupResponse(
        banner_title=f"경고: 요구사항 가이드 미확정 관련 작업 ({len(selected)}건)",
        banner_description="가이드 미달성 및 고객사 피드백이 지연되어 추가 조치 대기 중인 작업 리스트입니다.",
        rows=selected,
    )


async def get_task_view(db: AsyncSession, request_no: str, view_code: str) -> TaskViewResponse:
    result = await db.execute(
        select(TaskViewSnapshot, DataRequest)
        .join(DataRequest, DataRequest.id == TaskViewSnapshot.data_request_id)
        .where(DataRequest.request_no == request_no, TaskViewSnapshot.view_code == view_code)
    )
    row = result.one_or_none()
    if row is None:
        raise not_found("TASK_VIEW_NOT_FOUND", "작업 상세 데이터를 찾을 수 없습니다.")
    snapshot, data_request = row
    return TaskViewResponse(
        request_no=data_request.request_no,
        request_title=data_request.title,
        view_code=snapshot.view_code,
        payload=snapshot.payload,
    )


def _chart_buckets(
    period: DeveloperDashboardPeriod,
    now: datetime,
) -> tuple[datetime, list[tuple[datetime, str]], timedelta]:
    local_now = _to_dashboard_time(now)
    local_today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == DeveloperDashboardPeriod.DAILY:
        size = timedelta(hours=4)
        buckets = [
            (_to_utc(local_today + size * index), f"{index * 4:02d}:00")
            for index in range(6)
        ]
        return buckets[0][0], buckets, size
    if period == DeveloperDashboardPeriod.WEEKLY:
        start = local_today - timedelta(days=6)
        size = timedelta(days=1)
        buckets = [
            (_to_utc(start + size * index), (start + size * index).strftime("%m.%d"))
            for index in range(7)
        ]
        return buckets[0][0], buckets, size

    start = local_today.replace(day=1)
    size = timedelta(days=1)
    buckets = [
        (_to_utc(start + size * index), f"{index + 1}일")
        for index in range(local_now.day)
    ]
    return buckets[0][0], buckets, size


def _agent_status(latest: AgentMetric | None) -> str:
    if latest is None:
        return "unknown"
    if latest.outcome.upper() != "SUCCEEDED":
        return "error"
    if latest.latency_ms is not None and latest.latency_ms >= DELAY_THRESHOLD_MS:
        return "delayed"
    return "ok"


async def get_developer_dashboard(
    db: AsyncSession,
    period: DeveloperDashboardPeriod,
) -> DeveloperDashboardResponse:
    now = utcnow()
    local_now = _to_dashboard_time(now)
    today = _to_utc(local_now.replace(hour=0, minute=0, second=0, microsecond=0))
    month_start = _to_utc(local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    chart_start, bucket_specs, bucket_size = _chart_buckets(period, now)
    metrics_start = min(month_start, chart_start, now - timedelta(hours=24))

    metrics = list(
        (
            await db.scalars(
                select(AgentMetric)
                .where(AgentMetric.created_at >= metrics_start)
                .order_by(AgentMetric.created_at)
            )
        ).all()
    )

    month_metrics = [metric for metric in metrics if as_utc(metric.created_at) >= month_start]
    today_metrics = [metric for metric in metrics if as_utc(metric.created_at) >= today]
    month_tokens = sum(metric.input_tokens + metric.output_tokens for metric in month_metrics)
    today_tokens = sum(metric.input_tokens + metric.output_tokens for metric in today_metrics)
    month_cost = sum((metric.cost_usd or Decimal("0")) for metric in month_metrics)

    bucket_values = [
        {"input_tokens": 0, "output_tokens": 0, "cost_usd": Decimal("0")}
        for _ in bucket_specs
    ]
    for metric in metrics:
        metric_created_at = as_utc(metric.created_at)
        if metric_created_at < chart_start:
            continue
        bucket_index = int((metric_created_at - chart_start) // bucket_size)
        if 0 <= bucket_index < len(bucket_values):
            bucket_values[bucket_index]["input_tokens"] += metric.input_tokens
            bucket_values[bucket_index]["output_tokens"] += metric.output_tokens
            bucket_values[bucket_index]["cost_usd"] += metric.cost_usd or Decimal("0")

    token_series = [
        TokenUsagePointResponse(
            label=label,
            input_tokens=value["input_tokens"],
            output_tokens=value["output_tokens"],
            total_tokens=value["input_tokens"] + value["output_tokens"],
            cost_usd=float(value["cost_usd"]),
        )
        for (_, label), value in zip(bucket_specs, bucket_values, strict=True)
    ]

    agents = []
    for agent_key in AGENT_CARD_ORDER:
        agent_metrics = [metric for metric in metrics if metric.agent_name == agent_key]
        latest = agent_metrics[-1] if agent_metrics else None
        agents.append(
            AgentStatusResponse(
                agent_key=agent_key,
                name=AGENT_LABELS[agent_key],
                status=_agent_status(latest),
                last_response_at=as_utc(latest.created_at) if latest else None,
                latency_ms=latest.latency_ms if latest else None,
                today_throughput=sum(as_utc(metric.created_at) >= today for metric in agent_metrics),
            )
        )

    failure_rates = []
    last_24_hours = now - timedelta(hours=24)
    for agent_key in FAILURE_RATE_ORDER:
        recent = [
            metric
            for metric in metrics
            if metric.agent_name == agent_key and as_utc(metric.created_at) >= last_24_hours
        ]
        failed = sum(metric.outcome.upper() != "SUCCEEDED" for metric in recent)
        percent = round(failed / len(recent) * 100, 1) if recent else 0.0
        failure_rates.append(
            AgentFailureRateResponse(
                agent_key=agent_key,
                label=AGENT_LABELS[agent_key].removesuffix(" 에이전트"),
                failed_runs=failed,
                total_runs=len(recent),
                percent=percent,
            )
        )

    event_rows = (
        await db.execute(
            select(PipelineEvent, StageRun)
            .outerjoin(StageRun, StageRun.id == PipelineEvent.stage_run_id)
            .where(
                PipelineEvent.event_type == EventType.FAILED,
                PipelineEvent.occurred_at >= last_24_hours,
            )
            .order_by(PipelineEvent.occurred_at.desc())
            .limit(10)
        )
    ).all()
    error_logs = []
    for event, stage_run in event_rows:
        payload = event.payload or {}
        severity = event.severity.upper()
        if severity not in {"HIGH", "MEDIUM", "LOW"}:
            severity = "HIGH"
        agent_key = payload.get("agent_name")
        if not agent_key and stage_run is not None:
            agent_key = {
                "REQUIREMENT_ANALYSIS": "requirement-analysis-agent",
                "DATA_SELECTION": "data-selection-agent",
                "DATA_PROCESSING": "data-processing-agent",
                "DELIVERY": "delivery-pipeline",
            }.get(stage_run.stage_code)
        error_logs.append(
            DeveloperErrorLogResponse(
                occurred_at=event.occurred_at,
                agent=AGENT_LABELS.get(agent_key, agent_key or "시스템"),
                message=event.message.removeprefix("[DEMO] "),
                severity=severity,
            )
        )

    return DeveloperDashboardResponse(
        payload=DeveloperDashboardPayload(
            period=period,
            generated_at=now,
            summary=TokenUsageSummaryResponse(
                month_tokens=month_tokens,
                today_tokens=today_tokens,
                estimated_cost_usd=float(month_cost),
                estimated_cost_krw=round(month_cost * settings.dashboard_usd_to_krw_rate),
            ),
            token_series=token_series,
            agents=agents,
            failure_rates=failure_rates,
            error_logs=error_logs,
        )
    )


async def get_member_management(db: AsyncSession) -> MemberManagementResponse:
    employees = list((await db.scalars(select(Employee).order_by(Employee.created_at))).all())
    members = []
    for employee in employees:
        permissions = {permission.permission_code for permission in employee.permissions}
        if PermissionCode.EMPLOYEE_PERMISSION_MANAGE in permissions:
            role, role_bg, role_color = "관리자", "#e6f3f3", "#0f5a52"
        elif PermissionCode.EMPLOYEE_UPDATE in permissions:
            role, role_bg, role_color = "책임자", "#e6f0ff", "#0066ff"
        elif PermissionCode.DATA_PRODUCT_WRITE in permissions:
            role, role_bg, role_color = "선임", "#f3e8ff", "#8b5cf6"
        else:
            role, role_bg, role_color = "일반", "#f8f9fa", "#6b7280"
        members.append(
            MemberResponse(
                name=employee.name,
                user_id=employee.employee_code,
                role=role,
                role_bg=role_bg,
                role_color=role_color,
                part=employee.department,
                last_login_at=employee.updated_at.strftime("%Y.%m.%d %H:%M"),
                status="활성" if employee.status == EmployeeStatus.ACTIVE else "비활성",
            )
        )
    active_count = sum(member.status == "활성" for member in members)
    return MemberManagementResponse(
        total_count=len(members),
        active_count=active_count,
        inactive_count=len(members) - active_count,
        members=members,
    )
