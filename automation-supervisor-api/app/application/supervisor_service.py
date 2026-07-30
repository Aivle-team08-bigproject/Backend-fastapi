from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.model.strands_agent_client import StrandsAgentClient
from app.application.cache import build_cache_key
from app.application.ports.agent_client import AgentClient
from app.application.validation import validate_stage_output
from app.core.config import settings
from app.domain.enums import JobStatus, StageName, StageStatus
from app.domain.models import AutomationJob, AutomationStageRun, StageArtifactCache


class SupervisorService:
    stage_order = [
        StageName.REQUIREMENT_ANALYSIS,
        StageName.DATA_SELECTION,
        StageName.DATA_RETRIEVAL,
        StageName.DATA_PROCESSING,
    ]

    progress = {
        StageName.REQUIREMENT_ANALYSIS: 20,
        StageName.DATA_SELECTION: 40,
        StageName.DATA_RETRIEVAL: 65,
        StageName.DATA_PROCESSING: 90,
        StageName.HITL_REVIEW: 100,
    }

    def __init__(self, db: AsyncSession, agent_client: AgentClient | None = None):
        self.db = db
        self.agent_client = agent_client or StrandsAgentClient(fallback_to_stub=True)

    async def create_job(
        self,
        raw_requirement: str,
        requirement_id: int | None = None,
        source_csv_path: str | None = None,
    ) -> AutomationJob:
        job = AutomationJob(
            requirement_id=requirement_id,
            raw_requirement=raw_requirement,
            status=JobStatus.QUEUED.value,
            max_qa_iterations=settings.max_qa_iterations,
            final_result={
                "_source": {
                    "source_type": "preselected_csv",
                    "source_csv_path": source_csv_path,
                }
            },
        )
        self.db.add(job)
        await self.db.commit()
        await self.db.refresh(job)
        return await self.get_job(job.id)

    async def dispatch_next_worker(self, job_id: int) -> AutomationStageRun:
        """Supervisor Worker entry point: create exactly one stage Worker."""
        job = await self.get_job(job_id)
        if not job:
            raise ValueError("job not found")
        if job.status not in {
            JobStatus.QUEUED.value,
            JobStatus.SUPERVISOR_QUEUED.value,
            JobStatus.WAITING_RETRY.value,
        }:
            raise ValueError(f"supervisor cannot dispatch from status {job.status}")

        job.started_at = job.started_at or datetime.utcnow()
        worker = await self._dispatch_next_worker(job)
        await self.db.commit()
        return worker

    async def mark_supervisor_queued(self, job_id: int) -> AutomationJob:
        job = await self.get_job(job_id)
        if job is None:
            raise ValueError("job not found")
        if job.status not in {JobStatus.QUEUED.value, JobStatus.WAITING_RETRY.value}:
            raise ValueError(f"supervisor cannot be queued from status {job.status}")
        job.status = JobStatus.SUPERVISOR_QUEUED.value
        await self.db.commit()
        return await self.get_job(job_id)

    async def mark_queue_failed(self, job_id: int, error_message: str) -> AutomationJob:
        job = await self.get_job(job_id)
        if job is None:
            raise ValueError("job not found")
        if job.status == JobStatus.SUPERVISOR_QUEUED.value:
            job.status = JobStatus.WAITING_RETRY.value
            job.error_message = error_message
            await self.db.commit()
        return await self.get_job(job_id)

    async def run_worker(self, stage_id: int) -> AutomationJob:
        """Run one previously-created worker, persist its output, and stop."""
        stage = await self._get_stage(stage_id)
        if stage is None:
            raise ValueError("worker not found")
        if stage.status != StageStatus.PENDING.value:
            raise ValueError("worker is not pending")

        job = await self.get_job(stage.job_id)
        if job is None:
            raise ValueError("job not found")
        if job.status != JobStatus.WORKER_CREATED.value:
            raise ValueError(f"worker cannot run while job status is {job.status}")

        stage_name = StageName(stage.stage_name)
        stage.status = StageStatus.RUNNING.value
        stage.started_at = datetime.utcnow()
        job.status = JobStatus.RUNNING.value
        job.current_stage = stage_name.value
        await self.db.commit()

        try:
            output = await self.agent_client.run(
                self._agent_for(stage_name),
                stage.model_name or self._model_for(stage_name),
                stage.input_payload,
            )
            validation = validate_stage_output(stage_name, output)
        except Exception as exc:  # noqa: BLE001 - worker failures must be persisted
            output = {"_worker_error": str(exc)}
            validation = {
                "passed": False,
                "errors": [str(exc)],
                "failure_code": None,
            }

        stage.output_payload = output
        stage.validation_result = validation
        stage.completed_at = datetime.utcnow()
        job.progress_percent = self.progress[stage_name]

        if validation["passed"]:
            stage.status = StageStatus.COMPLETED.value
            artifacts = dict(job.final_result or {})
            artifacts[stage_name.value] = output
            job.final_result = artifacts
            job.status = JobStatus.WAITING_HITL.value
            job.current_stage = StageName.HITL_REVIEW.value
            job.error_message = None
            self.db.add(
                StageArtifactCache(
                    stage_id=stage.id,
                    cache_key=build_cache_key(stage.stage_name, stage.model_name or "", stage.input_payload),
                    artifact=output,
                )
            )
        else:
            stage.status = StageStatus.FAILED.value
            stage.error_message = "; ".join(validation["errors"])
            job.status = JobStatus.FAILED.value
            job.current_stage = stage_name.value
            job.error_message = stage.error_message

        await self.db.commit()
        return await self.get_job(job.id)

    async def submit_hitl_review(
        self,
        job_id: int,
        approved: bool,
        reviewer: str,
        natural_feedback: str,
        failure_code: str | None = None,
    ) -> AutomationJob:
        job = await self.get_job(job_id)
        if not job:
            raise ValueError("job not found")
        if job.status != JobStatus.WAITING_HITL.value:
            raise ValueError("job is not waiting for HITL review")

        review_output = {
            "approved": approved,
            "reviewer": reviewer,
            "natural_feedback": natural_feedback,
            "failure_code": failure_code,
        }
        stage = await self._create_stage_run(
            job_id=job.id,
            stage_name=StageName.HITL_REVIEW,
            model_name="human",
            payload={"artifacts": job.final_result},
        )
        stage.output_payload = review_output
        stage.validation_result = validate_stage_output(StageName.HITL_REVIEW, review_output)
        stage.started_at = datetime.utcnow()
        stage.completed_at = datetime.utcnow()

        if approved:
            stage.status = StageStatus.COMPLETED.value
            reviewed_stage = self._reviewed_stage(job)
            if reviewed_stage == StageName.DATA_PROCESSING:
                job.status = JobStatus.COMPLETED.value
                job.progress_percent = 100
                job.completed_at = datetime.utcnow()
            else:
                job.status = JobStatus.WAITING_RETRY.value
            await self.db.commit()
            return await self.get_job(job_id)

        stage.status = StageStatus.COMPLETED.value
        job.status = JobStatus.REJECTED.value
        job.error_message = natural_feedback or "client rejected the worker result"
        job.completed_at = datetime.utcnow()
        await self.db.commit()
        return await self.get_job(job_id)

    async def _dispatch_next_worker(
        self,
        job: AutomationJob,
        after_stage: StageName | None = None,
    ) -> AutomationStageRun:
        active = next(
            (
                stage
                for stage in job.stages
                if stage.status in {StageStatus.PENDING.value, StageStatus.RUNNING.value}
            ),
            None,
        )
        if active:
            raise ValueError("job already has an active worker")

        if job.rollback_to_stage:
            stage_name = StageName(job.rollback_to_stage)
            job.rollback_to_stage = None
        elif after_stage is not None:
            next_index = self._stage_index(after_stage) + 1
            if next_index >= len(self.stage_order):
                raise ValueError("no next worker")
            stage_name = self.stage_order[next_index]
        else:
            completed = {
                StageName(stage.stage_name)
                for stage in job.stages
                if stage.stage_name != StageName.HITL_REVIEW.value
                and stage.status in {StageStatus.COMPLETED.value, StageStatus.CACHED.value}
            }
            stage_name = next((name for name in self.stage_order if name not in completed), self.stage_order[-1])

        artifacts = dict(job.final_result or {})
        payload = self._payload_for(job, stage_name, artifacts)
        worker = await self._create_stage_run(
            job_id=job.id,
            stage_name=stage_name,
            model_name=self._model_for(stage_name),
            payload=payload,
        )
        job.status = JobStatus.WORKER_CREATED.value
        job.current_stage = stage_name.value
        job.error_message = None
        await self.db.flush()
        return worker

    def _reviewed_stage(self, job: AutomationJob) -> StageName:
        workers = [
            stage
            for stage in job.stages
            if stage.stage_name != StageName.HITL_REVIEW.value
            and stage.status in {StageStatus.COMPLETED.value, StageStatus.FAILED.value}
        ]
        if not workers:
            raise ValueError("no worker result to review")
        return StageName(max(workers, key=lambda item: item.run_order).stage_name)

    async def _get_stage(self, stage_id: int) -> AutomationStageRun | None:
        return await self.db.get(AutomationStageRun, stage_id)

    async def get_job(self, job_id: int) -> AutomationJob | None:
        result = await self.db.execute(
            select(AutomationJob)
            .where(AutomationJob.id == job_id)
            .options(selectinload(AutomationJob.stages))
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def _create_stage_run(
        self,
        job_id: int,
        stage_name: StageName,
        model_name: str,
        payload: dict,
    ) -> AutomationStageRun:
        result = await self.db.execute(select(AutomationStageRun).where(AutomationStageRun.job_id == job_id))
        run_order = len(result.scalars().all()) + 1
        stage = AutomationStageRun(
            job_id=job_id,
            stage_name=stage_name.value,
            model_name=model_name,
            input_payload=payload,
            run_order=run_order,
        )
        self.db.add(stage)
        await self.db.flush()
        return stage

    async def _mark_rolled_back(self, job_id: int, rollback_stage: StageName) -> None:
        rollback_index = self._stage_index(rollback_stage)
        rollback_names = [stage.value for stage in self.stage_order[rollback_index:]]
        result = await self.db.execute(
            select(AutomationStageRun).where(
                AutomationStageRun.job_id == job_id,
                AutomationStageRun.stage_name.in_(rollback_names),
            )
        )
        for stage in result.scalars().all():
            if stage.status in {StageStatus.COMPLETED.value, StageStatus.CACHED.value}:
                stage.status = StageStatus.ROLLED_BACK.value

    def _payload_for(self, job: AutomationJob, stage_name: StageName, artifacts: dict[str, dict]) -> dict:
        if stage_name == StageName.REQUIREMENT_ANALYSIS:
            return {"raw_requirement": job.raw_requirement}
        if stage_name == StageName.DATA_SELECTION:
            return {
                "raw_requirement": job.raw_requirement,
                "analysis": artifacts[StageName.REQUIREMENT_ANALYSIS.value],
                "available_data": ["merchant", "member_pseudonymized", "transaction_pseudonymized"],
            }
        if stage_name == StageName.DATA_RETRIEVAL:
            source = artifacts.get("_source") or {}
            return {
                "job_id": job.id,
                "raw_requirement": job.raw_requirement,
                "source_csv_path": source.get("source_csv_path"),
                "analysis": artifacts[StageName.REQUIREMENT_ANALYSIS.value],
                "selection": artifacts[StageName.DATA_SELECTION.value],
            }
        if stage_name == StageName.DATA_PROCESSING:
            return {
                "raw_requirement": job.raw_requirement,
                "analysis": artifacts[StageName.REQUIREMENT_ANALYSIS.value],
                "selection": artifacts[StageName.DATA_SELECTION.value],
                "retrieval": artifacts[StageName.DATA_RETRIEVAL.value],
            }
        return {"raw_requirement": job.raw_requirement, "artifacts": artifacts}

    def _model_for(self, stage_name: StageName) -> str:
        if stage_name == StageName.REQUIREMENT_ANALYSIS:
            return settings.requirement_analysis_model
        if stage_name == StageName.DATA_SELECTION:
            return settings.data_selection_model
        if stage_name == StageName.DATA_RETRIEVAL:
            return "deterministic-csv-retrieval-v1"
        if stage_name == StageName.DATA_PROCESSING:
            return settings.data_processing_model
        return settings.requirement_analysis_model

    def _agent_for(self, stage_name: StageName) -> str:
        return {
            StageName.REQUIREMENT_ANALYSIS: "requirement-analysis-agent",
            StageName.DATA_SELECTION: "data-selection-agent",
            StageName.DATA_RETRIEVAL: "data-retrieval-agent",
            StageName.DATA_PROCESSING: "data-processing-agent",
        }[stage_name]

    def _stage_index(self, stage_name: str | StageName) -> int:
        normalized = StageName(stage_name)
        return self.stage_order.index(normalized)
