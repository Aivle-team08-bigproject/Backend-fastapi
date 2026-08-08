from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.domains.notices.model import NoticeStatus


def _trim_required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("공백만 입력할 수 없습니다.")
    return value


class NoticeListItem(BaseModel):
    id: int
    title: str
    author_name: str
    published_at: datetime


class NoticeDetail(NoticeListItem):
    content: str


class NoticeListResponse(BaseModel):
    items: list[NoticeListItem]
    total_count: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class NoticeLatestResponse(BaseModel):
    item: NoticeListItem | None


class NoticeWriteRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20_000)
    status: NoticeStatus = NoticeStatus.DRAFT

    _title_required = field_validator("title")(_trim_required)
    _content_required = field_validator("content")(_trim_required)


class NoticeUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, min_length=1, max_length=20_000)
    status: NoticeStatus | None = None

    _title_required = field_validator("title")(_trim_required)
    _content_required = field_validator("content")(_trim_required)
