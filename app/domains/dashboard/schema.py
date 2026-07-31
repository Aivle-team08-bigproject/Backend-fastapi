from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


PriorityCode = Literal["REQUIREMENT", "SAMPLE", "FINAL"]
StageGroupCode = Literal[
    "REQUIREMENT_ANALYSIS",
    "SAMPLE_DATA",
    "FINAL_OUTPUT",
    "COMPLETED",
    "UNKNOWN",
]
DecisionStatus = Literal["pending", "approved", "changes_requested", "not_required"]
StatusGroupCode = Literal["waiting_review", "in_progress", "completed", "failed", "unknown"]


class DashboardTaskItemResponse(BaseModel):
    request_no: str
    client: str
    title: str
    assignee_code: str | None
    assignee_name: str
    stage_code: str | None
    stage_group_code: StageGroupCode
    stage_label: str
    status_code: str | None
    status_group_code: StatusGroupCode
    priority_code: PriorityCode | None
    decision_status: DecisionStatus
    requires_action: bool
    detail_route: str
    created_at: datetime
    updated_at: datetime


class DashboardPriorityCardResponse(BaseModel):
    priority_code: PriorityCode
    label: str
    count: int = Field(ge=0)
    detail_route: str


class PopularProductResponse(BaseModel):
    product_code: str
    product_name: str
    request_count: int = Field(ge=0)


class DashboardDeadlineTaskResponse(BaseModel):
    request_no: str
    client: str
    title: str
    assignee_name: str
    stage_label: str
    due_at: datetime
    detail_route: str


class DashboardResponse(BaseModel):
    generated_at: datetime
    priority_cards: list[DashboardPriorityCardResponse]
    priority_actions: list[DashboardTaskItemResponse]
    popular_products: list[PopularProductResponse]
    popular_products_unavailable_message: str
    approval_tasks: list[DashboardTaskItemResponse]
    deadline_tasks: list[DashboardDeadlineTaskResponse]
    active_task_count: int = Field(ge=0)


class DashboardTaskQuery(BaseModel):
    priority: PriorityCode | None = None
    stage: StageGroupCode | None = None
    page: int = Field(default=1, ge=1)
    page_size: Literal[30, 50, 100] = 30


class DashboardTaskListResponse(BaseModel):
    items: list[DashboardTaskItemResponse]
    total_count: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: Literal[30, 50, 100]


# The old name remains import-compatible for code that only references the
# response type, while its fields now describe the canonical dashboard DTO.
PractitionerDashboardResponse = DashboardResponse


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


class DeveloperDashboardPeriod(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class DeveloperDashboardModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class TokenUsageSummaryResponse(DeveloperDashboardModel):
    month_tokens: int = Field(alias="monthTokens")
    today_tokens: int = Field(alias="todayTokens")
    estimated_cost_usd: float = Field(alias="estimatedCostUsd")
    estimated_cost_krw: int = Field(alias="estimatedCostKrw")


class TokenUsagePointResponse(DeveloperDashboardModel):
    label: str
    input_tokens: int = Field(alias="inputTokens")
    output_tokens: int = Field(alias="outputTokens")
    total_tokens: int = Field(alias="totalTokens")
    cost_usd: float = Field(alias="costUsd")


class AgentStatusResponse(DeveloperDashboardModel):
    agent_key: str = Field(alias="agentKey")
    name: str
    status: Literal["ok", "delayed", "error", "unknown"]
    last_response_at: datetime | None = Field(alias="lastResponseAt")
    latency_ms: int | None = Field(alias="latencyMs")
    today_throughput: int = Field(alias="todayThroughput")


class AgentFailureRateResponse(DeveloperDashboardModel):
    agent_key: str = Field(alias="agentKey")
    label: str
    failed_runs: int = Field(alias="failedRuns")
    total_runs: int = Field(alias="totalRuns")
    percent: float


class DeveloperErrorLogResponse(DeveloperDashboardModel):
    occurred_at: datetime = Field(alias="occurredAt")
    agent: str
    message: str
    severity: Literal["HIGH", "MEDIUM", "LOW"]


class DeveloperDashboardPayload(DeveloperDashboardModel):
    period: DeveloperDashboardPeriod
    generated_at: datetime = Field(alias="generatedAt")
    summary: TokenUsageSummaryResponse
    token_series: list[TokenUsagePointResponse] = Field(alias="tokenSeries")
    agents: list[AgentStatusResponse]
    failure_rates: list[AgentFailureRateResponse] = Field(alias="failureRates")
    error_logs: list[DeveloperErrorLogResponse] = Field(alias="errorLogs")


class DeveloperDashboardResponse(BaseModel):
    payload: DeveloperDashboardPayload


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
