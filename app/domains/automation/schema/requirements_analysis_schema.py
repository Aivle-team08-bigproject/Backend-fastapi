from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domains.automation.model.requirements_analysis_model import AnalysisStatus


class AnalyzeRequirementRequest(BaseModel):
    raw_request: str = Field(min_length=1, max_length=8000)


class AnalysisResult(BaseModel):
    usage_purpose: str
    requested_data_summary: str
    requested_data_categories: dict[str, str]
    delivery_channel: str
    output_format: list[str]


class AnalyzeRequirementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    raw_request: str
    requested_by: str
    status: AnalysisStatus
    model_provider: str
    model_id: str
    analysis_result: AnalysisResult | None
    error_message: str | None
    created_at: datetime
