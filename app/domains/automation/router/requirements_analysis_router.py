from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.security_deps import CurrentAuth, get_current_auth
from app.db.session import get_db
from app.domains.automation.schema.requirements_analysis_schema import (
    AnalyzeRequirementRequest,
    AnalyzeRequirementResponse,
)
from app.domains.automation.service import requirements_analysis_service

router = APIRouter(
    prefix="/api/automation/requirements-analysis", tags=["automation-requirements-analysis"]
)


@router.post("", response_model=AnalyzeRequirementResponse)
async def analyze_requirement(
    payload: AnalyzeRequirementRequest,
    auth: CurrentAuth = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> AnalyzeRequirementResponse:
    run = await requirements_analysis_service.analyze_requirement(
        db, payload.raw_request, auth.employee.employee_code
    )
    return AnalyzeRequirementResponse.model_validate(run)


@router.get("", response_model=list[AnalyzeRequirementResponse])
async def list_analysis_runs(
    limit: int = Query(50, ge=1, le=200),
    auth: CurrentAuth = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[AnalyzeRequirementResponse]:
    runs = await requirements_analysis_service.list_runs(db, limit)
    return [AnalyzeRequirementResponse.model_validate(r) for r in runs]


@router.get("/{run_id}", response_model=AnalyzeRequirementResponse)
async def get_analysis_run(
    run_id: int,
    auth: CurrentAuth = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> AnalyzeRequirementResponse:
    run = await requirements_analysis_service.find_one(db, run_id)
    return AnalyzeRequirementResponse.model_validate(run)
