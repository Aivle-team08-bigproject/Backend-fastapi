from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from urllib.parse import quote
from zoneinfo import ZoneInfo

from sqlalchemy import case, cast, func, literal, or_, select
from sqlalchemy.types import DateTime as SqlDateTime
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import forbidden, not_found
from app.common.time_utils import as_utc, utcnow
from app.core.config import settings
from app.domains.dashboard.model import TaskViewSnapshot
from app.domains.dashboard.schema import (
    AgentFailureRateResponse,
    AgentStatusResponse,
    DashboardDeadlineTaskResponse,
    DashboardCalendarEventResponse,
    DashboardResponse,
    DashboardTaskItemResponse,
    DashboardTaskListResponse,
    DashboardTaskQuery,
    DashboardPriorityCardResponse,
    PopularProductResponse,
    DeveloperDashboardPayload,
    DeveloperDashboardPeriod,
    DeveloperDashboardResponse,
    DeveloperErrorLogResponse,
    MemberManagementResponse,
    MemberResponse,
    MyTaskStatusResponse,
    TaskLookupResponse,
    TaskRowResponse,
    TaskViewResponse,
    TaskDetailResponse,
    TaskStageDetailResponse,
    TaskArtifactDetailResponse,
    TaskHistoryResponse,
    AdminDashboardResponse,
    AssigneeProgressResponse,
    DashboardSummaryResponse,
    DashboardProgressResponse,
    DashboardStageProgressResponse,
    PersonalDashboardSummaryResponse,
    TokenUsagePointResponse,
    TokenUsageSummaryResponse,
)
from app.domains.pipeline.model import (
    AgentMetric,
    Artifact,
    Client,
    Contract,
    DataRequest,
    EventType,
    PipelineEvent,
    PipelineRun,
    Review,
    StageRun,
)
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

POPULAR_PRODUCTS_UNAVAILABLE_MESSAGE = "인기 상품 데이터는 제공되지 않습니다."
UNKNOWN_DETAIL_ROUTE = "/dashboard/tasks?stage=UNKNOWN"
DETAIL_ROUTE_BY_STAGE_GROUP = {
    "REQUIREMENT_ANALYSIS": "/tasks/review",
    "SAMPLE_DATA": "/tasks/sample-feedback",
    "FINAL_OUTPUT": "/tasks/final-feedback",
    "COMPLETED": "/tasks/complete",
}
PRIORITY_LABEL_BY_CODE = {
    "REQUIREMENT": "요구사항 검토 필요",
    "SAMPLE": "샘플 데이터 검토 필요",
    "FINAL": "최종 산출물 검토 필요",
}
WAITING_PRIORITY_BY_STATUS = {
    "WAITING_REQUIREMENT_REVIEW": "REQUIREMENT",
    "WAITING_SAMPLE_REVIEW": "SAMPLE",
    "WAITING_FINAL_REVIEW": "FINAL",
}
RECOGNIZED_REVIEW_TYPES = ("REQUIREMENT", "SAMPLE", "FINAL")
STAGE_GROUP_BY_STAGE_CODE = {
    "REQUIREMENT_ANALYSIS": "REQUIREMENT_ANALYSIS",
    "DATA_SELECTION": "SAMPLE_DATA",
    "SAMPLE_DATA": "SAMPLE_DATA",
    "DATA_PROCESSING": "FINAL_OUTPUT",
    "FINAL_OUTPUT": "FINAL_OUTPUT",
    "DELIVERY": "FINAL_OUTPUT",
    "COMPLETED": "COMPLETED",
}


@dataclass(frozen=True)
class _ProjectedTask:
    request_no: str
    run_id: int | None
    client: str
    title: str
    analysis_condition: dict
    owner_id: int | None
    assignee_code: str | None
    assignee_name: str
    stage_code: str | None
    stage_group_code: str
    stage_label: str
    status_code: str | None
    run_status: str | None
    current_stage: str | None
    progress_percent: int
    attempt_no: int | None
    rollback_to_stage: str | None
    stage_attempt_no: int | None
    status_group_code: str
    priority_code: str | None
    decision_status: str
    requires_action: bool
    detail_route: str
    created_at: datetime
    updated_at: datetime
    due_at: datetime | None
    contract_start_date: date | None
    contract_end_date: date | None


def _detail_route_for_task(stage_group_code: str, request_no: str, run_id: int | None) -> str:
    if stage_group_code in DETAIL_ROUTE_BY_STAGE_GROUP and run_id is not None:
        return f"/tasks/{quote(request_no, safe='')}/runs/{run_id}/detail"
    return UNKNOWN_DETAIL_ROUTE


def _latest_pipeline_run_subquery():
    ranked = select(
        PipelineRun.id.label("run_id"),
        PipelineRun.data_request_id.label("data_request_id"),
        PipelineRun.status.label("run_status"),
        PipelineRun.current_stage.label("current_stage"),
        PipelineRun.progress_percent.label("progress_percent"),
        PipelineRun.attempt_no.label("attempt_no"),
        PipelineRun.rollback_to_stage.label("rollback_to_stage"),
        func.row_number()
        .over(
            partition_by=PipelineRun.data_request_id,
            order_by=(
                PipelineRun.attempt_no.desc(),
                PipelineRun.created_at.desc(),
                PipelineRun.id.desc(),
            ),
        )
        .label("run_rank"),
    ).subquery("ranked_pipeline_runs")
    return select(ranked).where(ranked.c.run_rank == 1).subquery("latest_pipeline_runs")


def _latest_stage_run_subquery():
    ranked = select(
        StageRun.id.label("stage_run_id"),
        StageRun.pipeline_run_id.label("pipeline_run_id"),
        StageRun.stage_code.label("stage_code"),
        StageRun.status.label("stage_status"),
        StageRun.attempt_no.label("stage_attempt_no"),
        PipelineRun.current_stage.label("pipeline_current_stage"),
        func.row_number()
        .over(
            partition_by=StageRun.pipeline_run_id,
            order_by=(
                case((StageRun.stage_code == PipelineRun.current_stage, 0), else_=1),
                StageRun.attempt_no.desc(),
                StageRun.created_at.desc(),
                StageRun.id.desc(),
            ),
        )
        .label("stage_rank"),
    ).join(PipelineRun, PipelineRun.id == StageRun.pipeline_run_id).subquery("ranked_stage_runs")
    return select(ranked).where(ranked.c.stage_rank == 1).subquery("latest_stage_runs")


def _latest_review_subquery():
    ranked = select(
        Review.id.label("review_id"),
        Review.data_request_id.label("data_request_id"),
        Review.review_type.label("review_type"),
        Review.decision.label("review_decision"),
        func.row_number()
        .over(
            partition_by=Review.data_request_id,
            order_by=(Review.created_at.desc(), Review.id.desc()),
        )
        .label("review_rank"),
    ).subquery("ranked_reviews")
    return select(ranked).where(ranked.c.review_rank == 1).subquery("latest_reviews")


def _projection_query():
    latest_run = _latest_pipeline_run_subquery()
    latest_stage = _latest_stage_run_subquery()
    latest_review = _latest_review_subquery()

    stage_group = case(
        (latest_stage.c.stage_code.is_(None), literal("UNKNOWN")),
        *(
            (latest_stage.c.stage_code == stage_code, literal(stage_group))
            for stage_code, stage_group in STAGE_GROUP_BY_STAGE_CODE.items()
        ),
        else_=literal("UNKNOWN"),
    )
    waiting_priority = case(
        *(
            (latest_run.c.run_status == run_status, literal(priority))
            for run_status, priority in WAITING_PRIORITY_BY_STATUS.items()
        ),
        else_=literal(None),
    )
    review_priority = case(
        *(
            (latest_review.c.review_type == review_type, literal(review_type))
            for review_type in RECOGNIZED_REVIEW_TYPES
        ),
        else_=literal(None),
    )
    has_unknown_review = latest_review.c.review_type.is_not(None) & ~latest_review.c.review_type.in_(
        RECOGNIZED_REVIEW_TYPES
    )
    has_workflow_state = latest_run.c.run_id.is_not(None) & latest_stage.c.stage_code.is_not(None)
    priority = case(
        (stage_group == "COMPLETED", literal(None)),
        (~has_workflow_state, literal(None)),
        (has_unknown_review, literal(None)),
        (latest_run.c.run_status.in_(tuple(WAITING_PRIORITY_BY_STATUS)), waiting_priority),
        (latest_review.c.review_type.in_(RECOGNIZED_REVIEW_TYPES), review_priority),
        else_=literal(None),
    )
    decision_status = case(
        (latest_review.c.review_decision == "APPROVED", literal("approved")),
        (latest_review.c.review_decision == "CHANGES_REQUESTED", literal("changes_requested")),
        (
            latest_run.c.run_status.in_(tuple(WAITING_PRIORITY_BY_STATUS)) & priority.is_not(None),
            literal("pending"),
        ),
        else_=literal("not_required"),
    )
    status_code = func.coalesce(
        latest_stage.c.stage_status,
        latest_run.c.run_status,
        DataRequest.status,
    )
    status_group = case(
        (latest_stage.c.stage_code.is_(None), literal("unknown")),
        (latest_run.c.run_status.in_(tuple(WAITING_PRIORITY_BY_STATUS)), literal("waiting_review")),
        (status_code == "COMPLETED", literal("completed")),
        (status_code.in_(("FAILED", "CANCELLED")), literal("failed")),
        (status_code.in_(("PENDING", "RUNNING", "QUEUED")), literal("in_progress")),
        else_=literal("unknown"),
    )
    requires_action = case(
        (priority.is_(None), literal(False)),
        (decision_status.in_(("pending", "changes_requested")), literal(True)),
        else_=literal(False),
    )
    due_at = cast(
        DataRequest.analysis_condition["due_at"].as_string(),
        SqlDateTime(timezone=True),
    )
    active_contract = (
        select(
            Contract.data_request_id.label("data_request_id"),
            Contract.start_date.label("start_date"),
            Contract.end_date.label("end_date"),
        )
        .where(Contract.status == "ACTIVE")
        .subquery("active_contract")
    )

    return (
        select(
            DataRequest.request_no.label("request_no"),
            latest_run.c.run_id.label("run_id"),
            DataRequest.title.label("title"),
            DataRequest.analysis_condition.label("analysis_condition"),
            DataRequest.owner_id.label("owner_id"),
            DataRequest.created_at.label("created_at"),
            DataRequest.updated_at.label("updated_at"),
            Client.company_name.label("client"),
            Employee.employee_code.label("assignee_code"),
            func.coalesce(Employee.name, literal("미배정")).label("assignee_name"),
            latest_stage.c.stage_code.label("stage_code"),
            stage_group.label("stage_group_code"),
            status_code.label("status_code"),
            latest_run.c.run_status.label("run_status"),
            latest_run.c.current_stage.label("current_stage"),
            func.coalesce(latest_run.c.progress_percent, 0).label("progress_percent"),
            latest_run.c.attempt_no.label("attempt_no"),
            latest_run.c.rollback_to_stage.label("rollback_to_stage"),
            latest_stage.c.stage_attempt_no.label("stage_attempt_no"),
            status_group.label("status_group_code"),
            priority.label("priority_code"),
            decision_status.label("decision_status"),
            requires_action.label("requires_action"),
            due_at.label("due_at"),
            active_contract.c.start_date.label("contract_start_date"),
            active_contract.c.end_date.label("contract_end_date"),
        )
        .join(Client, Client.id == DataRequest.client_id)
        .outerjoin(active_contract, active_contract.c.data_request_id == DataRequest.id)
        .outerjoin(Employee, Employee.id == DataRequest.owner_id)
        .outerjoin(latest_run, latest_run.c.data_request_id == DataRequest.id)
        .outerjoin(latest_stage, latest_stage.c.pipeline_run_id == latest_run.c.run_id)
        .outerjoin(latest_review, latest_review.c.data_request_id == DataRequest.id)
    )


def _task_projection_from_row(row) -> _ProjectedTask:
    values = row._mapping
    stage_group_code = values["stage_group_code"]
    stage_label = {
        "REQUIREMENT_ANALYSIS": "요구사항 분석",
        "SAMPLE_DATA": "샘플 데이터",
        "FINAL_OUTPUT": "최종 산출물",
        "COMPLETED": "완료",
        "UNKNOWN": "상태 확인 필요",
    }.get(stage_group_code, "상태 확인 필요")
    return _ProjectedTask(
        request_no=values["request_no"],
        run_id=values["run_id"],
        client=values["client"],
        title=values["title"],
        analysis_condition=values["analysis_condition"] or {},
        owner_id=values["owner_id"],
        assignee_code=values["assignee_code"],
        assignee_name=values["assignee_name"],
        stage_code=values["stage_code"],
        stage_group_code=stage_group_code,
        stage_label=stage_label,
        status_code=values["status_code"],
        run_status=values["run_status"],
        current_stage=values["current_stage"],
        progress_percent=int(values["progress_percent"] or 0),
        attempt_no=values["attempt_no"],
        rollback_to_stage=values["rollback_to_stage"],
        stage_attempt_no=values["stage_attempt_no"],
        status_group_code=values["status_group_code"],
        priority_code=values["priority_code"],
        decision_status=values["decision_status"],
        requires_action=bool(values["requires_action"]),
        detail_route=_detail_route_for_task(stage_group_code, values["request_no"], values["run_id"]),
        created_at=values["created_at"],
        updated_at=values["updated_at"],
        due_at=values["due_at"],
        contract_start_date=values["contract_start_date"],
        contract_end_date=values["contract_end_date"],
    )


def _dashboard_task_item(task: _ProjectedTask) -> DashboardTaskItemResponse:
    return DashboardTaskItemResponse(
        request_no=task.request_no,
        run_id=task.run_id,
        client=task.client,
        title=task.title,
        assignee_code=task.assignee_code,
        assignee_name=task.assignee_name,
        stage_code=task.stage_code,
        stage_group_code=task.stage_group_code,
        stage_label=task.stage_label,
        status_code=task.status_code,
        status_group_code=task.status_group_code,
        priority_code=task.priority_code,
        decision_status=task.decision_status,
        requires_action=task.requires_action,
        progress_percent=task.progress_percent,
        due_at=task.due_at,
        detail_route=task.detail_route,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _legacy_task_row(task: _ProjectedTask) -> TaskRowResponse:
    metadata = task.analysis_condition
    return TaskRowResponse(
        request_no=task.request_no,
        run_id=task.run_id,
        detail_route=task.detail_route,
        client=task.client,
        data_type=metadata.get("data_type", "데이터 분석"),
        detail=metadata.get("detail", task.title),
        assignee=task.assignee_name,
        created_at=task.created_at.strftime("%Y.%m.%d"),
        updated_at=task.updated_at.strftime("%Y.%m.%d"),
        status={
            "REQUIREMENT_ANALYSIS": "요구사항 분석",
            "SAMPLE_DATA": "진행중",
            "FINAL_OUTPUT": "가공중",
            "COMPLETED": "완료",
            "UNKNOWN": "상태 확인 필요",
        }.get(task.stage_group_code, "상태 확인 필요"),
    )


def _is_completed(task: _ProjectedTask) -> bool:
    # status_code는 stage_status(현재 단계 자체의 실행 결과)까지 합쳐놓은 값이라
    # WAITING_*_REVIEW로 멈춘 작업도 "해당 단계는 COMPLETED"라서 status_code == "COMPLETED"가
    # 참이 된다. 전체 작업이 끝났는지는 waiting_review를 이미 우선 처리한 status_group_code로만
    # 판단해야 한다 — 아니면 검토 대기 중인 작업이 전부 "완료"로 잘못 걸러진다.
    return task.status_group_code == "completed"


async def _load_projected_tasks(
    db: AsyncSession,
    owner_id: int | None = None,
) -> list[_ProjectedTask]:
    query = _projection_query()
    if owner_id is not None:
        query = query.where(DataRequest.owner_id == owner_id)
    result = await db.execute(query.order_by(DataRequest.created_at.desc(), DataRequest.request_no.desc()))
    return [_task_projection_from_row(row) for row in result]


def _task_filters(projection, query: DashboardTaskQuery, owner_id: int | None = None) -> list:
    predicates = []
    if owner_id is not None:
        predicates.append(projection.c.owner_id == owner_id)
    if query.priority is not None:
        predicates.extend((
            projection.c.priority_code == query.priority,
            projection.c.requires_action.is_(True),
            projection.c.status_group_code != "completed",
        ))
    if query.stage is not None:
        predicates.append(projection.c.stage_group_code == query.stage)
    if query.status is not None:
        if query.status == "overdue":
            predicates.append(
                projection.c.due_at.is_not(None)
                & (projection.c.due_at < utcnow())
                & (projection.c.status_group_code != "completed")
            )
        else:
            predicates.append(projection.c.status_group_code == query.status)
    if query.assignee is not None:
        predicates.append(projection.c.assignee_code == query.assignee)
    if query.search:
        term = f"%{query.search.replace(' ', '').strip().lower()}%"

        def compact(column):
            return func.replace(func.lower(column), " ", "")

        predicates.append(
            or_(
                compact(projection.c.request_no).like(term),
                compact(projection.c.client).like(term),
                compact(projection.c.title).like(term),
                compact(projection.c.assignee_name).like(term),
            )
        )
    if query.created_from is not None:
        predicates.append(
            projection.c.created_at >= datetime.combine(query.created_from, time.min, tzinfo=ZoneInfo("UTC"))
        )
    if query.created_to is not None:
        predicates.append(
            projection.c.created_at < datetime.combine(
                query.created_to + timedelta(days=1), time.min, tzinfo=ZoneInfo("UTC")
            )
        )
    return predicates


def _popular_products(tasks: list[_ProjectedTask]) -> list[PopularProductResponse]:
    product_counts: Counter[str] = Counter()
    for task in tasks:
        product_name = task.analysis_condition.get("product_name")
        if isinstance(product_name, str) and product_name.strip():
            product_counts[product_name.strip()] += 1

    return [
        PopularProductResponse(
            product_code=f"PRODUCT-{index:03d}",
            product_name=product_name,
            request_count=request_count,
        )
        for index, (product_name, request_count) in enumerate(
            sorted(product_counts.items(), key=lambda item: (-item[1], item[0]))[:5],
            start=1,
        )
    ]


def _due_at_from_metadata(metadata: dict) -> datetime | None:
    due_at = metadata.get("due_at")
    if not isinstance(due_at, str):
        return None
    try:
        return as_utc(datetime.fromisoformat(due_at.replace("Z", "+00:00")))
    except ValueError:
        return None


def _deadline_tasks(tasks: list[_ProjectedTask]) -> list[DashboardDeadlineTaskResponse]:
    due_tasks = [
        (task, due_at)
        for task in tasks
        if not _is_completed(task)
        if (due_at := _due_at_from_metadata(task.analysis_condition)) is not None
    ]
    due_tasks.sort(key=lambda item: item[1])
    return [
        DashboardDeadlineTaskResponse(
            request_no=task.request_no,
            client=task.client,
            title=task.title,
            assignee_name=task.assignee_name,
            stage_label=task.stage_label,
            due_at=due_at,
            detail_route=task.detail_route,
        )
        for task, due_at in due_tasks[:5]
    ]


def _calendar_events(tasks: list[_ProjectedTask]) -> list[DashboardCalendarEventResponse]:
    tz = ZoneInfo(settings.dashboard_timezone)
    events: list[DashboardCalendarEventResponse] = []
    for task in tasks:
        if _is_completed(task):
            continue
        dates = (
            ("CONTRACT_START", task.contract_start_date),
            ("CONTRACT_END", task.contract_end_date),
            ("DELIVERY_DUE", task.due_at.date() if task.due_at else None),
        )
        for event_type, event_date in dates:
            if event_date is not None:
                events.append(
                    DashboardCalendarEventResponse(
                        request_no=task.request_no,
                        title=task.title,
                        client=task.client,
                        event_type=event_type,
                        event_date=datetime.combine(event_date, time.min, tzinfo=tz),
                        detail_route=task.detail_route,
                    )
                )
    return sorted(events, key=lambda event: (event.event_date, event.request_no, event.event_type))
async def get_dashboard_tasks(
    db: AsyncSession,
    query: DashboardTaskQuery,
    employee: Employee,
    permissions: set[PermissionCode] | None = None,
) -> DashboardTaskListResponse:
    projection = _projection_query().subquery("dashboard_task_projection")
    if query.scope == "all" and not (permissions and PermissionCode.CONTRACT_MANAGE in permissions):
        raise forbidden("FORBIDDEN", "전체 작업 조회 권한이 없습니다.")
    owner_id = None if query.scope == "all" else employee.id
    predicates = _task_filters(projection, query, owner_id=owner_id)
    filtered = select(projection).where(*predicates)
    total_count = int(
        await db.scalar(select(func.count()).select_from(filtered.subquery("filtered_dashboard_tasks"))) or 0
    )
    result = await db.execute(
        filtered
        .order_by(projection.c.created_at.desc(), projection.c.request_no.desc())
        .limit(query.page_size)
        .offset((query.page - 1) * query.page_size)
    )
    items = [_dashboard_task_item(_task_projection_from_row(row)) for row in result]
    return DashboardTaskListResponse(
        scope=query.scope,
        items=items,
        total_count=total_count,
        page=query.page,
        page_size=query.page_size,
    )


def _personal_stage_progress(tasks: list[_ProjectedTask]) -> list[DashboardStageProgressResponse]:
    stage_codes = ("REQUIREMENT_ANALYSIS", "DATA_SELECTION", "DATA_PROCESSING")
    result: list[DashboardStageProgressResponse] = []
    for stage_code in stage_codes:
        current = [task for task in tasks if task.current_stage == stage_code]
        if any(task.status_group_code == "waiting_review" for task in current):
            status = "WAITING_REVIEW"
        elif any(task.status_group_code == "failed" for task in current):
            status = "FAILED"
        elif current:
            status = "RUNNING"
        elif tasks and all(_is_completed(task) for task in tasks):
            status = "COMPLETED"
        else:
            status = "PENDING"
        result.append(
            DashboardStageProgressResponse(
                code=stage_code,
                status=status,
                progress_percent=max((task.progress_percent for task in current), default=0),
            )
        )
    return result


async def get_practitioner_dashboard(db: AsyncSession, employee: Employee) -> DashboardResponse:
    projected_tasks = await _load_projected_tasks(db, owner_id=employee.id)
    popular_products = _popular_products(projected_tasks)
    action_items = [
        task for task in projected_tasks if task.requires_action and not _is_completed(task)
    ]
    approval_items = [
        task for task in action_items if task.decision_status == "pending"
    ]
    priority_cards = [
        DashboardPriorityCardResponse(
            priority_code=priority_code,
            label=PRIORITY_LABEL_BY_CODE[priority_code],
            count=sum(task.priority_code == priority_code for task in action_items),
            detail_route=f"/dashboard/tasks?priority={priority_code}",
        )
        for priority_code in PRIORITY_LABEL_BY_CODE
    ]
    return DashboardResponse(
        scope="mine",
        generated_at=utcnow(),
        summary=PersonalDashboardSummaryResponse(
            total_count=len(projected_tasks),
            active_count=sum(not _is_completed(task) for task in projected_tasks),
            approval_count=sum(task.status_group_code == "waiting_review" for task in projected_tasks),
            failed_count=sum(task.status_group_code == "failed" for task in projected_tasks),
            completion_rate=round(
                sum(_is_completed(task) for task in projected_tasks) / len(projected_tasks) * 100,
                1,
            ) if projected_tasks else 0,
        ),
        progress=DashboardProgressResponse(
            percent=round(sum(task.progress_percent for task in projected_tasks) / len(projected_tasks)) if projected_tasks else 0,
            current_stage=next(
                (task.current_stage for task in projected_tasks if not _is_completed(task)),
                "COMPLETED" if projected_tasks and all(_is_completed(task) for task in projected_tasks) else None,
            ),
            stages=_personal_stage_progress(projected_tasks),
        ),
        priority_cards=priority_cards,
        priority_actions=[_dashboard_task_item(task) for task in action_items[:5]],
        popular_products=popular_products,
        popular_products_unavailable_message=(
            POPULAR_PRODUCTS_UNAVAILABLE_MESSAGE if not popular_products else ""
        ),
        approval_tasks=[_dashboard_task_item(task) for task in approval_items[:5]],
        deadline_tasks=_deadline_tasks(projected_tasks),
        calendar_events=_calendar_events(projected_tasks),
        active_task_count=sum(not _is_completed(task) for task in projected_tasks),
    )


async def get_admin_dashboard(db: AsyncSession) -> AdminDashboardResponse:
    tasks = await _load_projected_tasks(db)
    now = utcnow()
    deadline_soon_limit = now + timedelta(hours=48)
    active = [task for task in tasks if not _is_completed(task)]
    waiting = [task for task in tasks if task.status_group_code == "waiting_review"]
    failed = [task for task in tasks if task.status_group_code == "failed"]
    overdue = [
        task for task in active
        if task.due_at is not None and task.due_at < now
    ]
    deadline_soon = [
        task for task in active
        if task.due_at is not None and now <= task.due_at <= deadline_soon_limit
    ]

    assignee_groups: dict[str | None, list[_ProjectedTask]] = {}
    for task in tasks:
        assignee_groups.setdefault(task.assignee_code, []).append(task)
    assignee_progress = []
    for assignee_code, group in sorted(
        assignee_groups.items(),
        key=lambda item: (item[0] is None, item[0] or ""),
    ):
        assignee_progress.append(
            AssigneeProgressResponse(
                assignee_code=assignee_code,
                assignee_name=group[0].assignee_name,
                total_count=len(group),
                completed_count=sum(_is_completed(task) for task in group),
                waiting_review_count=sum(task.status_group_code == "waiting_review" for task in group),
                failed_count=sum(task.status_group_code == "failed" for task in group),
                progress_percent=round(sum(task.progress_percent for task in group) / len(group), 1),
            )
        )

    return AdminDashboardResponse(
        scope="all",
        generated_at=now,
        summary=DashboardSummaryResponse(
            total_count=len(tasks),
            active_count=len(active),
            waiting_review_count=len(waiting),
            failed_count=len(failed),
            overdue_count=len(overdue),
            deadline_soon_count=len(deadline_soon),
        ),
        assignee_progress=assignee_progress,
        attention_items={
            "deadline_soon": [_dashboard_task_item(task) for task in sorted(deadline_soon, key=lambda task: task.due_at or now)],
            "overdue": [_dashboard_task_item(task) for task in sorted(overdue, key=lambda task: task.due_at or now)],
            "repeated_failures": [
                _dashboard_task_item(task)
                for task in failed
                if (task.stage_attempt_no or 0) >= 3
            ],
            "final_outputs_for_review": [
                _dashboard_task_item(task)
                for task in tasks
                if task.stage_group_code == "FINAL_OUTPUT" and task.status_group_code == "waiting_review"
            ],
        },
    )


async def get_task_detail(
    db: AsyncSession,
    request_no: str,
    run_id: int,
    employee: Employee,
    permissions: set[PermissionCode],
) -> TaskDetailResponse:
    run = await db.get(PipelineRun, run_id)
    request = await db.scalar(select(DataRequest).where(DataRequest.request_no == request_no))
    if run is None or request is None or run.data_request_id != request.id:
        raise not_found("TASK_NOT_FOUND", "작업을 찾을 수 없습니다.")

    is_admin = PermissionCode.CONTRACT_MANAGE in permissions
    if not is_admin and request.owner_id != employee.id:
        raise not_found("TASK_NOT_FOUND", "작업을 찾을 수 없습니다.")

    assignee = await db.get(Employee, request.owner_id) if request.owner_id else None
    stage_runs = list(
        (
            await db.scalars(
                select(StageRun)
                .where(StageRun.pipeline_run_id == run.id)
                .order_by(StageRun.stage_code, StageRun.attempt_no)
            )
        ).all()
    )
    artifacts = list(
        (
            await db.scalars(
                select(Artifact)
                .where(Artifact.pipeline_run_id == run.id)
                .order_by(Artifact.created_at, Artifact.id)
            )
        ).all()
    )
    reviews = list(
        (
            await db.scalars(
                select(Review)
                .where(Review.data_request_id == request.id)
                .order_by(Review.created_at, Review.id)
            )
        ).all()
    )
    review_by_stage = {
        review.stage_run_id: review.decision
        for review in reviews
        if review.stage_run_id is not None
    }
    available_actions: list[str] = []
    if run.status in WAITING_PRIORITY_BY_STATUS:
        available_actions = ["APPROVE", "REQUEST_CHANGES"]
    elif run.status == "FAILED":
        available_actions = ["RETRY"]
    elif run.status == "COMPLETED":
        available_actions = ["DOWNLOAD"]

    return TaskDetailResponse(
        request_no=request.request_no,
        run_id=run.id,
        title=request.title,
        assignee_code=assignee.employee_code if assignee else None,
        assignee_name=assignee.name if assignee else "미배정",
        run_status=run.status,
        current_stage=run.current_stage,
        progress_percent=run.progress_percent,
        attempt_no=run.attempt_no,
        rollback_to_stage=run.rollback_to_stage,
        error_message=run.error_message,
        stages=[
            TaskStageDetailResponse(
                stage_code=stage.stage_code,
                status=stage.status,
                progress_percent=(
                    run.progress_percent
                    if stage.stage_code == run.current_stage
                    else 100
                    if stage.status == "COMPLETED"
                    else 0
                ),
                attempt_no=stage.attempt_no,
                executor=stage.executor,
                review_status=review_by_stage.get(stage.id),
                artifacts=[
                    TaskArtifactDetailResponse(
                        artifact_id=artifact.id,
                        artifact_type=artifact.artifact_type,
                        storage_key=artifact.storage_key,
                        mime_type=artifact.mime_type,
                        size_bytes=artifact.size_bytes,
                        pii_scan_status=artifact.pii_scan_status,
                    )
                    for artifact in artifacts
                    if artifact.stage_run_id == stage.id
                ],
                created_at=stage.created_at,
                started_at=stage.started_at,
                completed_at=stage.completed_at,
                error_message=stage.error_message,
            )
            for stage in stage_runs
        ],
        available_actions=available_actions,
        history=[
            TaskHistoryResponse(
                review_type=review.review_type,
                decision=review.decision,
                feedback=review.feedback,
                reviewer_name=review.reviewer_name,
                created_at=review.created_at,
            )
            for review in reviews
        ],
    )


async def get_my_task_status(db: AsyncSession, employee: Employee) -> MyTaskStatusResponse:
    projected_tasks = await _load_projected_tasks(db)
    assigned_tasks = [task for task in projected_tasks if task.assignee_code == employee.employee_code]
    active_tasks = [task for task in assigned_tasks if not _is_completed(task)]
    completed_count = len(assigned_tasks) - len(active_tasks)
    completion_rate = round(completed_count / len(assigned_tasks) * 100, 1) if assigned_tasks else 0.0
    return MyTaskStatusResponse(
        employee_code=employee.employee_code,
        user_name=employee.name,
        department=employee.department.name if employee.department else "",
        active_count=len(active_tasks),
        urgent_count=sum(
            task.stage_group_code == "REQUIREMENT_ANALYSIS" for task in active_tasks
        ),
        completed_count=completed_count,
        completion_rate=completion_rate,
        tasks=[_legacy_task_row(task) for task in assigned_tasks],
    )


async def get_task_lookup(db: AsyncSession) -> TaskLookupResponse:
    projected_tasks = await _load_projected_tasks(db)
    selected = [
        _legacy_task_row(task)
        for task in projected_tasks
        if task.analysis_condition.get("alert_code") == "REQUIREMENT_GUIDE"
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


def _to_dashboard_time(value: datetime) -> datetime:
    return as_utc(value).astimezone(ZoneInfo(settings.dashboard_timezone))


def _to_utc(value: datetime) -> datetime:
    """대시보드의 모든 시간 비교를 UTC aware datetime으로 통일한다."""
    return as_utc(value)


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
                part=employee.department.name if employee.department else "",
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
