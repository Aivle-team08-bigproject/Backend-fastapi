"""Validate that the frontend integration seed contains every required scenario."""

import asyncio

from sqlalchemy import func, select

from app.common.time_utils import utcnow
from app.db.session import AsyncSessionLocal, engine
from app.domains.pipeline.model import Artifact, DataRequest, PipelineEvent, PipelineRun, Review, StageRun


EXPECTED_REQUESTS = {
    "E2E-REQ-001",
    "E2E-SAMPLE-001",
    "E2E-FINAL-001",
    "E2E-COMPLETE-001",
    "E2E-FAIL-001",
    "E2E-FAIL-002",
    "E2E-DEADLINE-001",
    "E2E-OVERDUE-001",
}


async def verify() -> None:
    async with AsyncSessionLocal() as db:
        requests = set(
            (
                await db.scalars(
                    select(DataRequest.request_no).where(DataRequest.request_no.in_(EXPECTED_REQUESTS))
                )
            ).all()
        )
        missing = EXPECTED_REQUESTS - requests
        if missing:
            raise RuntimeError(f"missing E2E requests: {sorted(missing)}")

        artifact_count = await db.scalar(
            select(func.count()).select_from(Artifact).where(
                Artifact.pipeline_run_id.in_(
                    select(PipelineRun.id).where(PipelineRun.data_request_id.in_(
                        select(DataRequest.id).where(DataRequest.request_no.in_(EXPECTED_REQUESTS))
                    ))
                )
            )
        )
        review_count = await db.scalar(
            select(func.count()).select_from(Review).where(Review.data_request_id.in_(
                select(DataRequest.id).where(DataRequest.request_no.in_(EXPECTED_REQUESTS))
            ))
        )
        failure_event_count = await db.scalar(
            select(func.count()).select_from(PipelineEvent).where(
                PipelineEvent.event_type == "failed",
                PipelineEvent.pipeline_run_id.in_(
                    select(PipelineRun.id).where(PipelineRun.data_request_id.in_(
                        select(DataRequest.id).where(DataRequest.request_no.in_(EXPECTED_REQUESTS))
                    ))
                ),
            )
        )
        repeated_attempt_count = await db.scalar(
            select(func.count()).select_from(StageRun).where(
                StageRun.attempt_no >= 3,
                StageRun.pipeline_run_id.in_(
                    select(PipelineRun.id).where(PipelineRun.data_request_id.in_(
                        select(DataRequest.id).where(DataRequest.request_no == "E2E-FAIL-002")
                    ))
                ),
            )
        )
        if not artifact_count or not review_count or not failure_event_count or not repeated_attempt_count:
            raise RuntimeError(
                "E2E auxiliary data is incomplete: "
                f"artifacts={artifact_count}, reviews={review_count}, "
                f"failure_events={failure_event_count}, repeated_attempts={repeated_attempt_count}"
            )
        print(
            "E2E data verified: "
            f"requests={len(requests)}, artifacts={artifact_count}, reviews={review_count}, "
            f"failure_events={failure_event_count}, repeated_attempts={repeated_attempt_count}, "
            f"checked_at={utcnow().isoformat()}"
        )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(verify())
