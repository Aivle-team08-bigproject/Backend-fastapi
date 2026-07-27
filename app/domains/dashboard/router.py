from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.security_deps import CurrentAuth, get_current_auth
from app.db.session import get_db
from app.domains.dashboard.schema import (
    DeveloperDashboardResponse,
    DeveloperDashboardPeriod,
    MemberManagementResponse,
    MyTaskStatusResponse,
    PractitionerDashboardResponse,
    TaskLookupResponse,
    TaskViewResponse,
)
from app.domains.dashboard.service import (
    get_developer_dashboard,
    get_member_management,
    get_my_task_status,
    get_practitioner_dashboard,
    get_task_lookup,
    get_task_view,
)


router = APIRouter(prefix="/api/v1", tags=["dashboard"])


@router.get("/dashboard", response_model=PractitionerDashboardResponse)
async def practitioner_dashboard(db: AsyncSession = Depends(get_db)) -> PractitionerDashboardResponse:
    return await get_practitioner_dashboard(db)


@router.get("/dashboard/my-tasks", response_model=MyTaskStatusResponse)
async def my_task_status(
    auth: CurrentAuth = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MyTaskStatusResponse:
    return await get_my_task_status(db, auth.employee)


@router.get("/dashboard/task-lookup", response_model=TaskLookupResponse)
async def task_lookup(db: AsyncSession = Depends(get_db)) -> TaskLookupResponse:
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


@router.get("/tasks/{request_no}/views/{view_code}", response_model=TaskViewResponse)
async def task_view(request_no: str, view_code: str, db: AsyncSession = Depends(get_db)) -> TaskViewResponse:
    return await get_task_view(db, request_no, view_code)
