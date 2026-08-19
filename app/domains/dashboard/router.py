from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.security_deps import CurrentAuth, get_current_auth, require_permission
from app.db.session import get_db
from app.domains.dashboard.schema import (
    DashboardResponse,
    AdminDashboardResponse,
    DashboardTaskListResponse,
    DashboardTaskQuery,
    DeveloperDashboardResponse,
    DeveloperDashboardPeriod,
    MemberManagementResponse,
    PriorityCode,
    StageGroupCode,
    StatusGroupCode,
    TaskLookupResponse,
    TaskDetailResponse,
    TaskViewResponse,
)
from app.domains.employees.model import PermissionCode
from app.domains.dashboard.service import (
    get_dashboard_tasks,
    get_admin_dashboard,
    get_developer_dashboard,
    get_member_management,
    get_practitioner_dashboard,
    get_task_lookup,
    get_task_view,
    get_task_detail,
)


router = APIRouter(prefix="/api/v1", tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardResponse)
async def practitioner_dashboard(
    auth: CurrentAuth = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> DashboardResponse:
    return await get_practitioner_dashboard(db, auth.employee)


@router.get("/dashboard/overview", response_model=AdminDashboardResponse)
async def admin_dashboard_overview(
    auth: CurrentAuth = Depends(require_permission(PermissionCode.CONTRACT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> AdminDashboardResponse:
    return await get_admin_dashboard(db)


@router.get("/dashboard/tasks", response_model=DashboardTaskListResponse)
async def dashboard_tasks(
    scope: Literal["mine", "all"] = Query(default="mine"),
    priority: PriorityCode | None = Query(default=None),
    stage: StageGroupCode | None = Query(default=None),
    search: str | None = Query(default=None, max_length=100),
    status: StatusGroupCode | None = Query(default=None),
    assignee: str | None = Query(default=None, max_length=40),
    created_from: date | None = Query(default=None),
    created_to: date | None = Query(default=None),
    due_from: date | None = Query(default=None),
    due_to: date | None = Query(default=None),
    created_sort: Literal["asc", "desc"] = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30),
    auth: CurrentAuth = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> DashboardTaskListResponse:
    if page_size not in (30, 50, 100):
        raise HTTPException(
            status_code=422,
            detail="page_size must be one of 30, 50, or 100",
        )
    if priority is not None and status is not None and status != "waiting_review":
        raise HTTPException(
            status_code=422,
            detail="priority filter is only meaningful with status=waiting_review (a task has a pending priority action only while awaiting review); combining it with another status always returns zero results",
        )
    query = DashboardTaskQuery(
        scope=scope,
        priority=priority,
        stage=stage,
        search=search,
        status=status,
        assignee=assignee,
        created_from=created_from,
        created_to=created_to,
        due_from=due_from,
        due_to=due_to,
        created_sort=created_sort,
        page=page,
        page_size=page_size,
    )
    return await get_dashboard_tasks(db, query, auth.employee, auth.permissions)


# Deprecated: 내 작업 현황 화면은 개인 대시보드(`/dashboard`)로 통합되어 비활성화했다.
# 기존 schema/service 구현은 복구 가능하도록 보존한다.
# @router.get("/dashboard/my-tasks", response_model=MyTaskStatusResponse)
# async def my_task_status(
#     auth: CurrentAuth = Depends(get_current_auth),
#     db: AsyncSession = Depends(get_db),
# ) -> MyTaskStatusResponse:
#     return await get_my_task_status(db, auth.employee)


@router.get("/dashboard/task-lookup", response_model=TaskLookupResponse)
async def task_lookup(
    auth: CurrentAuth = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> TaskLookupResponse:
    return await get_task_lookup(db)


@router.get("/dashboard/developer", response_model=DeveloperDashboardResponse)
async def developer_dashboard(
    period: DeveloperDashboardPeriod = Query(default=DeveloperDashboardPeriod.DAILY),
    db: AsyncSession = Depends(get_db),
) -> DeveloperDashboardResponse:
    return await get_developer_dashboard(db, period)


@router.get("/dashboard/members", response_model=MemberManagementResponse)
async def member_management(db: AsyncSession = Depends(get_db)) -> MemberManagementResponse:
    return await get_member_management(db)


@router.get("/tasks/{request_no:path}/views/{view_code:path}", response_model=TaskViewResponse)
async def task_view(
    request_no: str,
    view_code: str,
    auth: CurrentAuth = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> TaskViewResponse:
    return await get_task_view(db, request_no, view_code)


@router.get(
    "/tasks/{request_no:path}/runs/{run_id}/detail",
    response_model=TaskDetailResponse,
)
async def task_detail(
    request_no: str,
    run_id: int,
    auth: CurrentAuth = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> TaskDetailResponse:
    return await get_task_detail(db, request_no, run_id, auth.employee, auth.permissions)
