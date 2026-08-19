"""현재 DB의 시연용 더미 작업 요구사항을 제목 기반 상세 문장으로 보정한다."""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import AsyncSessionLocal, engine
from app.domains.pipeline.model import DataRequest
from scripts.demo_requirements import build_demo_requirement


def is_demo_request(request: DataRequest) -> bool:
    metadata = request.analysis_condition or {}
    return bool(
        metadata.get("demo_dashboard")
        or metadata.get("demo_priority_calendar")
        or request.request_no.startswith(("REQ-2024-", "REQ-20260714-"))
    )


async def update() -> int:
    changed = 0
    async with AsyncSessionLocal() as session:
        requests = (await session.scalars(select(DataRequest))).all()
        for request in requests:
            if not is_demo_request(request):
                continue
            requirement = build_demo_requirement(request.title, request.business_purpose)
            if request.raw_requirement == requirement:
                continue
            request.raw_requirement = requirement
            request.updated_at = datetime.now(timezone.utc)
            changed += 1
        await session.commit()
    return changed


async def main() -> None:
    try:
        print(f"updated={await update()}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
