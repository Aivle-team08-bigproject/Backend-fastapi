from pydantic import BaseModel, Field


class Pagination(BaseModel):
    page: int = Field(ge=1)
    size: int = Field(ge=1, le=100)
    total: int
