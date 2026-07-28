from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.model.strands_agent_client import StrandsAgentClient
from app.application.cache import build_cache_key
from app.application.event_publisher import publish_job_event
from app.application.ports.agent_client import AgentClient
from app.application.validation import validate_stage_output
from app.core.config import settings
from app.domain.enums import JobStatus, StageName, StageStatus
from app.domain.models import AutomationJob, AutomationStageRun, StageArtifactCache
from app.policies.rollback_policy import classify_hitl_feedback, rollback_stage_for


class SupervisorService:
    stage_order = [
        StageName.REQUIREMENT_ANALYSIS,
        StageName.DATA_SELECTION,
        StageName.DATA_PROCESSING,
    ]

    progress = {
        StageName.REQUIREMENT_ANALYSIS: 25,
        StageName.DATA_SELECTION: 50,
        StageName.DATA_PROCESSING: 90,
        StageName.HITL_REVIEW: 100,
    }

    def __init__(self, db: AsyncSession, agent_client: AgentClient | None = None):
        self.db = db
        self.agent_client = agent_client or StrandsAgentClient(fallback_to_stub=True)

    async def create_job(self, raw_requirement: str, requirement_id: int | None = None) -> AutomationJob:
        job = AutomationJob(
            requirement_id=requirement_id,
            raw_requirement=raw_requirement,
            status=JobStatus.QUEUED.value,
            max_qa_iterations=settings.max_qa_iterations,
        )
        self.db.add(job)
        await self.db.commit()
        await self.db.refresh(job)
        return job

    async def run_job(self, job_id: int) -> AutomationJob:
        job = await self.get_job(job_id)
        if not job:
            raise ValueError("job not found")

        job.status = JobStatus.RUNNING.value
        job.started_at = job.started_at or datetime.utcnow()
        await self.db.flush()

        while job.qa_iteration < job.max_qa_iterations:
            artifacts: dict[str, dict] = {}
            start_index = self._stage_index(job.rollback_to_stage) if job.rollback_to_stage else 0
            job.rollback_to_stage = None

            for stage_name in self.stage_order[start_index:]:
                stage = await self.run_stage(job, stage_name, artifacts)
                if stage.status == StageStatus.FAILED.value:
                    job.status = JobStatus.FAILED.value
                    job.error_message = stage.error_message
                    await self.db.commit()
                    return await self.get_job(job_id)
                artifacts[stage_name.value] = stage.output_payload

            job.status = JobStatus.WAITING_HITL.value
            job.current_stage = StageName.HITL_REVIEW.value
            job.progress_percent = 90
            job.final_result = artifacts
            await self.db.commit()
            return await self.get_job(job_id)

        job.status = JobStatus.FAILED.value
        job.error_message = "final QA failed and max iteration reached"
        job.completed_at = datetime.utcnow()
        await self.db.commit()
        return await self.get_job(job_id)

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

        resolved_failure_code = failure_code
        if not approved and not resolved_failure_code:
            resolved_failure_code = classify_hitl_feedback(natural_feedback).value

        review_output = {
            "approved": approved,
            "reviewer": reviewer,
            "natural_feedback": natural_feedback,
            "failure_code": resolved_failure_code,
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
            job.status = JobStatus.COMPLETED.value
            job.progress_percent = 100
            job.completed_at = datetime.utcnow()
            await self.db.commit()
            return await self.get_job(job_id)

        stage.status = StageStatus.FAILED.value
        job.qa_iteration += 1
        rollback_stage = rollback_stage_for(resolved_failure_code)
        job.rollback_to_stage = rollback_stage.value

        if job.qa_iteration >= job.max_qa_iterations:
            job.status = JobStatus.FAILED.value
            job.error_message = "HITL review rejected and max iteration reached"
            job.completed_at = datetime.utcnow()
        else:
            job.status = JobStatus.WAITING_RETRY.value
            job.current_stage = rollback_stage.value
            await self._mark_rolled_back(job.id, rollback_stage)

        await self.db.commit()
        return await self.get_job(job_id)

    async def run_stage(
        self,
        job: AutomationJob,
        stage_name: StageName,
        previous_artifacts: dict[str, dict],
    ) -> AutomationStageRun:
        model_name = self._model_for(stage_name)
        payload = self._payload_for(job, stage_name, previous_artifacts)
        stage = await self._create_stage_run(job.id, stage_name, model_name, payload)

        cached = await self._find_cached(stage, model_name, payload)
        if cached:
            stage.status = StageStatus.CACHED.value
            stage.output_payload = cached.artifact
            stage.validation_result = {"passed": True, "cached": True}
            job.current_stage = stage_name.value
            job.progress_percent = self.progress[stage_name]
            await self.db.flush()
            return stage

        job.current_stage = stage_name.value
        stage.status = StageStatus.RUNNING.value
        stage.started_at = datetime.utcnow()
        await self.db.flush()

        agent_name = self._agent_for(stage_name)
        output = await self.agent_client.run(agent_name, model_name, payload)
        validation = validate_stage_output(stage_name, output)

        stage.output_payload = output
        stage.validation_result = validation
        stage.completed_at = datetime.utcnow()
        job.progress_percent = self.progress[stage_name]

        if validation["passed"]:
            stage.status = StageStatus.COMPLETED.value
            self.db.add(
                StageArtifactCache(
                    stage_id=stage.id,
                    cache_key=build_cache_key(stage_name.value, model_name, payload),
                    artifact=output,
                )
            )
        else:
            stage.status = StageStatus.FAILED.value
            stage.error_message = "; ".join(validation["errors"])
            job.rollback_to_stage = rollback_stage_for(validation.get("failure_code")).value

        await self.db.flush()
        return stage

    async def start_job(self, job_id: int) -> AutomationStageRun:
        """Celery 경로의 진입점(Supervisor 워커). 첫 스테이지의 stage_run을 등록하고 반환한다.

        실제 스테이지 실행은 여기서 하지 않는다 — 호출자(run_supervisor_job_task)가
        반환된 stage_run.id로 run_stage_task를 dispatch해서 하위 워커에 넘긴다.
        """
        job = await self.get_job(job_id)
        if not job:
            raise ValueError("job not found")

        job.status = JobStatus.RUNNING.value
        job.started_at = job.started_at or datetime.utcnow()

        start_index = self._stage_index(job.rollback_to_stage) if job.rollback_to_stage else 0
        job.rollback_to_stage = None
        stage_name = self.stage_order[start_index]

        artifacts = await self._load_artifacts(job.id)
        stage = await self._dispatch_stage(job, stage_name, artifacts)
        await self.db.commit()
        await publish_job_event(
            job.id,
            {
                "type": "stage.queued",
                "stage_name": stage.stage_name,
                "job_status": job.status,
            },
        )
        return stage

    async def execute_stage(self, stage_run_id: int) -> tuple[AutomationStageRun, AutomationStageRun | None]:
        """Celery 경로의 하위 Worker. 이미 등록된 stage_run 하나를 실행하고,

        성공 시 다음 스테이지의 stage_run을 등록해 반환한다(호출자가 그걸로 다음
        run_stage_task를 dispatch한다). 마지막 스테이지거나 실패하면 None을 반환한다.
        """
        stage = await self._get_stage_run(stage_run_id)
        job = await self.get_job(stage.job_id)
        if not job:
            raise ValueError("job not found")
        stage_name = StageName(stage.stage_name)

        cached = await self._find_cached(stage, stage.model_name, stage.input_payload)
        if cached:
            stage.status = StageStatus.CACHED.value
            stage.output_payload = cached.artifact
            stage.validation_result = {"passed": True, "cached": True}
        else:
            stage.status = StageStatus.RUNNING.value
            stage.started_at = datetime.utcnow()
            await self.db.commit()
            await publish_job_event(
                job.id,
                {
                    "type": "stage.running",
                    "stage_name": stage.stage_name,
                    "job_status": job.status,
                },
            )

            agent_name = self._agent_for(stage_name)
            output = await self.agent_client.run(agent_name, stage.model_name, stage.input_payload)
            validation = validate_stage_output(stage_name, output)

            stage.output_payload = output
            stage.validation_result = validation
            stage.completed_at = datetime.utcnow()

            if validation["passed"]:
                stage.status = StageStatus.COMPLETED.value
                self.db.add(
                    StageArtifactCache(
                        stage_id=stage.id,
                        cache_key=build_cache_key(stage_name.value, stage.model_name, stage.input_payload),
                        artifact=output,
                    )
                )
            else:
                stage.status = StageStatus.FAILED.value
                stage.error_message = "; ".join(validation["errors"])
                job.rollback_to_stage = rollback_stage_for(validation.get("failure_code")).value

        job.progress_percent = self.progress[stage_name]
        await self.db.flush()

        next_stage: AutomationStageRun | None = None
        if stage.status == StageStatus.FAILED.value:
            job.status = JobStatus.FAILED.value
            job.error_message = stage.error_message
        else:
            next_index = self._stage_index(stage_name) + 1
            if next_index < len(self.stage_order):
                artifacts = await self._load_artifacts(job.id)
                next_stage = await self._dispatch_stage(job, self.stage_order[next_index], artifacts)
            else:
                job.status = JobStatus.WAITING_HITL.value
                job.current_stage = StageName.HITL_REVIEW.value
                job.progress_percent = 90
                job.final_result = await self._load_artifacts(job.id)

        await self.db.commit()
        await publish_job_event(
            job.id,
            {
                "type": "stage.completed" if stage.status != StageStatus.FAILED.value else "stage.failed",
                "stage_name": stage.stage_name,
                "stage_status": stage.status,
                "job_status": job.status,
                "progress_percent": job.progress_percent,
                "next_stage": next_stage.stage_name if next_stage else None,
            },
        )
        return stage, next_stage

    async def _dispatch_stage(
        self,
        job: AutomationJob,
        stage_name: StageName,
        artifacts: dict[str, dict],
    ) -> AutomationStageRun:
        model_name = self._model_for(stage_name)
        payload = self._payload_for(job, stage_name, artifacts)
        stage = await self._create_stage_run(job.id, stage_name, model_name, payload)
        job.current_stage = stage_name.value
        await self.db.flush()
        return stage

    async def _load_artifacts(self, job_id: int) -> dict[str, dict]:
        result = await self.db.execute(
            select(AutomationStageRun)
            .where(
                AutomationStageRun.job_id == job_id,
                AutomationStageRun.status.in_([StageStatus.COMPLETED.value, StageStatus.CACHED.value]),
            )
            .order_by(AutomationStageRun.run_order)
        )
        return {stage.stage_name: stage.output_payload for stage in result.scalars().all()}

    async def _get_stage_run(self, stage_run_id: int) -> AutomationStageRun:
        result = await self.db.execute(
            select(AutomationStageRun).where(AutomationStageRun.id == stage_run_id)
        )
        stage = result.scalar_one_or_none()
        if not stage:
            raise ValueError("stage run not found")
        return stage

    async def get_job(self, job_id: int) -> AutomationJob | None:
        result = await self.db.execute(
            select(AutomationJob).where(AutomationJob.id == job_id).options(selectinload(AutomationJob.stages))
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

    async def _find_cached(self, stage: AutomationStageRun, model_name: str, payload: dict) -> StageArtifactCache | None:
        key = build_cache_key(stage.stage_name, model_name, payload)
        result = await self.db.execute(
            select(StageArtifactCache)
            .join(AutomationStageRun)
            .where(
                AutomationStageRun.job_id == stage.job_id,
                AutomationStageRun.stage_name == stage.stage_name,
                StageArtifactCache.cache_key == key,
            )
            .order_by(StageArtifactCache.id.desc())
        )
        return result.scalar_one_or_none()

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
        if stage_name == StageName.DATA_PROCESSING:
            return {
                "raw_requirement": job.raw_requirement,
                "analysis": artifacts[StageName.REQUIREMENT_ANALYSIS.value],
                "selection": artifacts[StageName.DATA_SELECTION.value],
            }
        return {"raw_requirement": job.raw_requirement, "artifacts": artifacts}

    def _model_for(self, stage_name: StageName) -> str:
        if stage_name == StageName.REQUIREMENT_ANALYSIS:
            return settings.requirement_analysis_model
        if stage_name == StageName.DATA_SELECTION:
            return settings.data_selection_model
        if stage_name == StageName.DATA_PROCESSING:
            return settings.data_processing_model
        return settings.requirement_analysis_model

    def _agent_for(self, stage_name: StageName) -> str:
        return {
            StageName.REQUIREMENT_ANALYSIS: "requirement-analysis-agent",
            StageName.DATA_SELECTION: "data-selection-agent",
            StageName.DATA_PROCESSING: "data-processing-agent",
        }[stage_name]

    def _stage_index(self, stage_name: str | StageName) -> int:
        normalized = StageName(stage_name)
        return self.stage_order.index(normalized)
