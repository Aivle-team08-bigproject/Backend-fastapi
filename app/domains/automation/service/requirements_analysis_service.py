import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import bad_gateway, not_found
from app.domains.automation.agents import requirements_analysis_client
from app.domains.automation.model.requirements_analysis_model import (
    AnalysisStatus,
    RequirementsAnalysisRun,
)


async def analyze_requirement(
    db: AsyncSession, raw_request: str, requested_by: str
) -> RequirementsAnalysisRun:
    # requirements_analysis_client.invoke()는 원칙적으로 예외를 던지지 않는다(모든 실패가
    # ok=False + error_message로 정규화됨) — 그래도 예상 못한 버그에 대비해 방어적으로 감싼다.
    try:
        result = await asyncio.to_thread(requirements_analysis_client.invoke, raw_request)
    except Exception as exc:  # noqa: BLE001 — 정말 예상 못한 예외까지 502로 통일
        result = {"ok": False, "data": None, "error_message": str(exc)}

    if not result["ok"]:
        run = RequirementsAnalysisRun(
            raw_request=raw_request,
            requested_by=requested_by,
            status=AnalysisStatus.FAILED,
            model_provider=requirements_analysis_client.MODEL_PROVIDER,
            model_id=requirements_analysis_client.MODEL_ID,
            error_message=result["error_message"],
        )
        db.add(run)
        await db.commit()
        raise bad_gateway(
            "REQUIREMENTS_ANALYSIS_FAILED",
            "요구사항 분석 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
        )

    run = RequirementsAnalysisRun(
        raw_request=raw_request,
        requested_by=requested_by,
        status=AnalysisStatus.SUCCEEDED,
        model_provider=requirements_analysis_client.MODEL_PROVIDER,
        model_id=requirements_analysis_client.MODEL_ID,
        analysis_result=result["data"],
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
