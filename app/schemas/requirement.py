from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Priority, RequirementStatus


class RequirementCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    business_purpose: str | None = None
    acceptance_criteria: str | None = None
    requester: str | None = Field(default=None, max_length=100)
    owner: str | None = Field(default=None, max_length=100)
    priority: Priority = Priority.MEDIUM
    due_date: datetime | None = None
    actor: str = Field(default="system", max_length=100)


class RequirementUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1)
    business_purpose: str | None = None
    acceptance_criteria: str | None = None
    requester: str | None = Field(default=None, max_length=100)
    owner: str | None = Field(default=None, max_length=100)
    priority: Priority | None = None
    due_date: datetime | None = None
    actor: str = Field(default="system", max_length=100)


class RequirementStatusChange(BaseModel):
    status: RequirementStatus
    reason: str | None = None
    actor: str = Field(default="system", max_length=100)


class RequirementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    business_purpose: str | None
    acceptance_criteria: str | None
    requester: str | None
    owner: str | None
    priority: Priority
    status: RequirementStatus
    due_date: datetime | None
    progress: int = 0
    task_count: int = 0
    completed_task_count: int = 0
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime


class RequirementStatusHistoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    requirement_id: int
    from_status: RequirementStatus | None
    to_status: RequirementStatus
    reason: str | None
    changed_by: str
    changed_at: datetime


class RequirementListResponse(BaseModel):
    items: list[RequirementRead]
    page: int
    size: int
    total: int
