from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domains.requirements.model.enums import ProvideFormat, RequirementStatus


class RequirementCreate(BaseModel):
    """등록 폼(3필드) 기준 - /tasks/register 화면과 대응.

    인수인계서 확정사항: 자연어 3필드만 받는다
    (business_purpose / processing_request / provide_format).
    """

    client_id: int | None = None
    requester_name: str | None = Field(default=None, max_length=100)
    owner_id: int | None = None
    owner_name: str | None = Field(default=None, max_length=100)

    business_purpose: str | None = None
    processing_request: str = Field(min_length=1)
    provide_format: ProvideFormat

    usage_period: str | None = Field(default=None, max_length=100)
    analysis_condition: str | None = None
    sample_email: str | None = Field(default=None, max_length=200)


class RequirementStatusChange(BaseModel):
    status: RequirementStatus


class RequirementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_no: str
    client_id: int | None
    requester_name: str | None
    owner_id: int | None
    owner_name: str | None
    business_purpose: str | None
    processing_request: str
    provide_format: ProvideFormat
    usage_period: str | None
    analysis_condition: str | None
    sample_email: str | None
    status: RequirementStatus
    created_at: datetime
    updated_at: datetime


class RequirementListResponse(BaseModel):
    items: list[RequirementRead]
    page: int
    size: int
    total: int