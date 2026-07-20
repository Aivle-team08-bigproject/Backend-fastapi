import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import bad_gateway, not_found
from app.core.config import settings
from app.domains.automation.agents import requirements_analysis_agent
from app.domains.automation.model.requirements_analysis_model import (
    AnalysisStatus,
    RequirementsAnalysisRun,
)


async def analyze_requirement(
    db: AsyncSession, raw_request: str, requested_by: str
) -> RequirementsAnalysisRun:
    try:
        parsed = await asyncio.to_thread(requirements_analysis_agent.analyze, raw_request)
    except Exception as exc:  # noqa: BLE001 — 외부 LLM 호출 실패 원인 불문 기록 후 502로 통일
        run = RequirementsAnalysisRun(
            raw_request=raw_request,
            requested_by=requested_by,
            status=AnalysisStatus.FAILED,
            model_provider=settings.requirements_analysis_model_provider,
            model_id=settings.requirements_analysis_model_id,
            error_message=str(exc),
        )
        db.add(run)
        await db.commit()
        raise bad_gateway(
            "REQUIREMENTS_ANALYSIS_FAILED",
            "요구사항 분석 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
        ) from exc

    run = RequirementsAnalysisRun(
        raw_request=raw_request,
        requested_by=requested_by,
        status=AnalysisStatus.SUCCEEDED,
        model_provider=settings.requirements_analysis_model_provider,
        model_id=settings.requirements_analysis_model_id,
        summary=parsed["summary"],
        stage_prompts={
            "data_selection_prompt": parsed["data_selection_prompt"],
            "data_processing_prompt": parsed["data_processing_prompt"],
            "qa_prompt": parsed["qa_prompt"],
        },
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def find_one(db: AsyncSession, run_id: int) -> RequirementsAnalysisRun:
    run = await db.get(RequirementsAnalysisRun, run_id)
    if run is None:
        raise not_found("ANALYSIS_RUN_NOT_FOUND", "요구사항 분석 기록을 찾을 수 없습니다.")
    return run


async def list_runs(db: AsyncSession, limit: int = 50) -> list[RequirementsAnalysisRun]:
    result = await db.execute(
        select(RequirementsAnalysisRun)
        .order_by(RequirementsAnalysisRun.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
