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
