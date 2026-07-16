from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import Priority, TaskStatus


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    assignee: str | None = Field(default=None, max_length=100)
    priority: Priority = Priority.MEDIUM
    start_date: datetime | None = None
    due_date: datetime | None = None
    actor: str = Field(default="system", max_length=100)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.start_date and self.due_date and self.due_date < self.start_date:
            raise ValueError("due_date는 start_date보다 빠를 수 없습니다.")
        return self


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    assignee: str | None = Field(default=None, max_length=100)
    priority: Priority | None = None
    progress: int | None = Field(default=None, ge=0, le=100)
    start_date: datetime | None = None
    due_date: datetime | None = None
    actor: str = Field(default="system", max_length=100)


class TaskStatusChange(BaseModel):
    status: TaskStatus
    reason: str | None = None
    actor: str = Field(default="system", max_length=100)


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    requirement_id: int
    title: str
    description: str | None
    assignee: str | None
    priority: Priority
    status: TaskStatus
    progress: int
    start_date: datetime | None
    due_date: datetime | None
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime


class TaskStatusHistoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    from_status: TaskStatus | None
    to_status: TaskStatus
    reason: str | None
    changed_by: str
    changed_at: datetime
