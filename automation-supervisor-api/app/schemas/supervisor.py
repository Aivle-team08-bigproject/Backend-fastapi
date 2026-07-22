from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class JobCreate(BaseModel):
    raw_requirement: str = Field(min_length=1)
    requirement_id: int | None = None


class HitlReviewCreate(BaseModel):
    approved: bool
    reviewer: str = Field(min_length=1, max_length=100)
    natural_feedback: str = Field(default="", max_length=4000)
    failure_code: str | None = None


class StageRead(BaseModel):
    id: int
    stage_name: str
    status: str
    model_name: str | None
    input_payload: dict[str, Any]
    output_payload: dict[str, Any]
    validation_result: dict[str, Any]
    error_message: str | None
    run_order: int
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class JobRead(BaseModel):
    id: int
    requirement_id: int | None
    raw_requirement: str
    status: str
    current_stage: str | None
    progress_percent: int
    qa_iteration: int
    max_qa_iterations: int
    rollback_to_stage: str | None
    error_message: str | None
    final_result: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    stages: list[StageRead] = []

    model_config = {"from_attributes": True}
