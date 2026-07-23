from pydantic import BaseModel


class StatCardResponse(BaseModel):
    label: str
    value: int
    unit: str
    caption: str
    highlight: bool = False


class WarningCardResponse(BaseModel):
    title: str
    count_label: str
    count_bg: str
    count_color: str
    description: str
    foot_note: str
    action_to: str


class PreferredItemResponse(BaseModel):
    rank: int
    title: str
    subtitle: str
    tag: str
    tag_bg: str
    tag_color: str


class SupplementItemResponse(BaseModel):
    title: str
    note: str
    note_color: str
    tag: str
    tag_bg: str
    tag_color: str


class TaskRowResponse(BaseModel):
    request_no: str
    client: str
    data_type: str
    detail: str
    assignee: str
    created_at: str
    updated_at: str
    status: str


class PractitionerDashboardResponse(BaseModel):
    stat_cards: list[StatCardResponse]
    alert_banner_count: int
    warning_cards: list[WarningCardResponse]
    preferred_items: list[PreferredItemResponse]
    supplement_items: list[SupplementItemResponse]
    task_rows: list[TaskRowResponse]
    page_size: int


class MyTaskStatusResponse(BaseModel):
    employee_code: str
    user_name: str
    department: str
    active_count: int
    urgent_count: int
    completed_count: int
    completion_rate: float
    tasks: list[TaskRowResponse]


class TaskLookupResponse(BaseModel):
    banner_title: str
    banner_description: str
    rows: list[TaskRowResponse]


class TaskViewResponse(BaseModel):
    request_no: str
    request_title: str
    view_code: str
    payload: dict


class DeveloperDashboardResponse(BaseModel):
    payload: dict


class MemberResponse(BaseModel):
    name: str
    user_id: str
    role: str
    role_bg: str
    role_color: str
    part: str
    last_login_at: str
    status: str


class MemberManagementResponse(BaseModel):
    total_count: int
    active_count: int
    inactive_count: int
    members: list[MemberResponse]
