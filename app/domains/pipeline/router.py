from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.domains.pipeline.schema import (
    CreateDataRequestRequest,
    CreateDataRequestResponse,
    PipelineRunResponse,
)
from app.domains.pipeline.service import create_data_request, get_pipeline_run

router = APIRouter(prefix="/api/v1", tags=["pipeline"])


@router.post(
    "/data-requests",
    response_model=CreateDataRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_request(
    payload: CreateDataRequestRequest,
    db: AsyncSession = Depends(get_db),
) -> CreateDataRequestResponse:
    return await create_data_request(db, payload)


@router.get("/runs/{run_id}", response_model=PipelineRunResponse)
async def get_run(
    run_id: int,
    db: AsyncSession = Depends(get_db),
) -> PipelineRunResponse:
    return await get_pipeline_run(db, run_id)
