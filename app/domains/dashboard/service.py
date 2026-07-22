from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import not_found
from app.domains.dashboard.model import DashboardAlert, DashboardInsight, SystemDashboardSnapshot, TaskViewSnapshot
from app.domains.dashboard.schema import (
    PractitionerDashboardResponse,
    DeveloperDashboardResponse,
    MemberManagementResponse,
    MemberResponse,
    PreferredItemResponse,
    StatCardResponse,
    SupplementItemResponse,
    TaskLookupResponse,
    TaskRowResponse,
    TaskViewResponse,
    WarningCardResponse,
)
from app.domains.pipeline.model import Client, DataRequest
from app.domains.employees.model.employee_model import Employee, EmployeeStatus, PermissionCode


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


async def get_developer_dashboard(db: AsyncSession) -> DeveloperDashboardResponse:
    snapshot = await db.scalar(
        select(SystemDashboardSnapshot).where(SystemDashboardSnapshot.snapshot_code == "DEVELOPER_OVERVIEW")
    )
    if snapshot is None:
        raise not_found("DEVELOPER_DASHBOARD_NOT_FOUND", "개발자 대시보드 데이터를 찾을 수 없습니다.")
    return DeveloperDashboardResponse(payload=snapshot.payload)


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
