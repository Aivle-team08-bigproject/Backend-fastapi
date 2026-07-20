from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domains.automation.model.requirements_analysis_model import AnalysisStatus


class AnalyzeRequirementRequest(BaseModel):
    raw_request: str = Field(min_length=1, max_length=8000)


class StagePrompts(BaseModel):
    data_selection_prompt: str
    data_processing_prompt: str
    qa_prompt: str


class AnalyzeRequirementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    raw_request: str
    requested_by: str
    status: AnalysisStatus
    model_provider: str
    model_id: str
    summary: str | None
    stage_prompts: StagePrompts | None
    error_message: str | None
    created_at: datetime
