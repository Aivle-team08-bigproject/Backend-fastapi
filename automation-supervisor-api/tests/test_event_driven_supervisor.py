import asyncio

from app.application.supervisor_service import SupervisorService
from app.domain.enums import JobStatus, StageName, StageStatus
from app.domain.models import AutomationJob, AutomationStageRun


class FakeDb:
    async def commit(self):
        return None

    async def flush(self):
        return None

    async def refresh(self, _value):
        return None

    def add(self, _value):
        return None


class FakeAgentClient:
    async def run(self, agent_name: str, _model_name: str, _payload: dict) -> dict:
        if agent_name == "requirement-analysis-agent":
            return {
                "usage_purpose": "research",
                "requested_data_sentence": "결제 데이터 분석",
                "categories": {"지역": "서울"},
                "delivery_channel": "api",
                "output_formats": ["csv"],
            }
        if agent_name == "data-selection-agent":
            return {
                "selected_tables": [{"table": "transaction_pseudonymized", "reason": "결제 분석"}],
                "selection_query": {"vector_similarity": True, "top_k": 20, "filters": {"지역": "서울"}},
                "sample_columns": [
                    {
                        "name": "지역",
                        "data_type": "string",
                        "is_predicted": False,
                        "description": "조회 지역",
                    },
                    {
                        "name": "결제건수",
                        "data_type": "integer",
                        "is_predicted": True,
                        "description": "합성 결제 건수",
                    },
                ],
                "sample_rows": [
                    {"지역": "서울", "결제건수": index}
                    for index in range(1, 6)
                ],
                "sample_metadata": {
                    "is_synthetic": True,
                    "sample_count": 5,
                    "notice": "실제 고객 데이터가 아닌 형식 확인용 예시 데이터입니다.",
                },
            }
        if agent_name == "data-retrieval-agent":
            return {
                "source_type": "preselected_csv",
                "input_csv_path": _payload["source_csv_path"],
                "input_sha256": "input-test-checksum",
                "input_row_count": 4,
                "source_csv_path": _payload["source_csv_path"],
                "source_sha256": "test-checksum",
                "encoding": "utf-8-sig",
                "row_count": 2,
                "columns": ["customer_id", "amount"],
                "applied_filters": [
                    {
                        "filter": "지역",
                        "requested": "서울",
                        "columns": ["region"],
                        "resolved_values": ["서울"],
                    }
                ],
                "unmapped_filters": [],
                "selection_snapshot": _payload["selection"],
                "raw_rows_stored_in_database": False,
            }
        if agent_name == "data-processing-agent":
            return {
                "processed_columns": ["customer_id", "amount"],
                "api_result": {"items": [], "meta": {"row_count": 2}},
                "csv_columns": ["customer_id", "amount"],
                "visualization": None,
                "report": None,
                "processing_explanation": {},
                "quality_report": {"input_row_count": 2, "output_row_count": 2},
            }
        raise AssertionError(f"unexpected worker: {agent_name}")


class InMemorySupervisor(SupervisorService):
    def __init__(self):
        super().__init__(FakeDb(), FakeAgentClient())
        self.job = AutomationJob(
            id=1,
            raw_requirement="서울 결제 데이터를 CSV로 제공",
            status=JobStatus.QUEUED.value,
            progress_percent=0,
            qa_iteration=0,
            max_qa_iterations=3,
            final_result={
                "_source": {
                    "source_type": "preselected_csv",
                    "source_csv_path": "/data/selected.csv",
                }
            },
        )
        self.job.stages = []
        self._next_stage_id = 1

    async def get_job(self, job_id: int):
        return self.job if job_id == self.job.id else None

    async def _get_stage(self, stage_id: int):
        return next((stage for stage in self.job.stages if stage.id == stage_id), None)

    async def _create_stage_run(self, job_id, stage_name, model_name, payload):
        stage = AutomationStageRun(
            id=self._next_stage_id,
            job_id=job_id,
            stage_name=stage_name.value,
            status=StageStatus.PENDING.value,
            model_name=model_name,
            input_payload=payload,
            output_payload={},
            validation_result={},
            run_order=len(self.job.stages) + 1,
        )
        self._next_stage_id += 1
        self.job.stages.append(stage)
        return stage


def test_supervisor_creates_one_worker_then_stops():
    async def scenario():
        service = InMemorySupervisor()

        await service.dispatch_next_worker(1)
        job = await service.get_job(1)

        assert job.status == JobStatus.WORKER_CREATED.value
        assert len(job.stages) == 1
        assert job.stages[0].stage_name == StageName.REQUIREMENT_ANALYSIS.value
        assert job.stages[0].status == StageStatus.PENDING.value

    asyncio.run(scenario())


def test_worker_stores_result_and_approval_dispatches_next_worker():
    async def scenario():
        service = InMemorySupervisor()
        await service.dispatch_next_worker(1)

        job = await service.run_worker(1)
        assert job.status == JobStatus.WAITING_HITL.value
        assert job.final_result[StageName.REQUIREMENT_ANALYSIS.value]["categories"] == {"지역": "서울"}

        job = await service.submit_hitl_review(
            job_id=1,
            approved=True,
            reviewer="client-1",
            natural_feedback="승인",
        )
        await service.dispatch_next_worker(1)
        job = await service.get_job(1)

        assert job.status == JobStatus.WORKER_CREATED.value
        assert job.current_stage == StageName.DATA_SELECTION.value
        assert job.stages[-1].stage_name == StageName.DATA_SELECTION.value
        assert job.stages[-1].status == StageStatus.PENDING.value
        assert job.stages[-1].input_payload["analysis"]["categories"] == {"지역": "서울"}

    asyncio.run(scenario())


def test_approval_chain_dispatches_retrieval_before_processing():
    async def approve(service):
        return await service.submit_hitl_review(
            job_id=1,
            approved=True,
            reviewer="client-1",
            natural_feedback="승인",
        )

    async def scenario():
        service = InMemorySupervisor()

        await service.dispatch_next_worker(1)
        await service.run_worker(1)
        await approve(service)
        await service.dispatch_next_worker(1)

        await service.run_worker(3)
        await approve(service)
        await service.dispatch_next_worker(1)
        job = await service.get_job(1)
        retrieval_worker = job.stages[-1]
        assert retrieval_worker.stage_name == StageName.DATA_RETRIEVAL.value
        assert retrieval_worker.input_payload["source_csv_path"] == "/data/selected.csv"

        await service.run_worker(retrieval_worker.id)
        await approve(service)
        await service.dispatch_next_worker(1)
        job = await service.get_job(1)
        processing_worker = job.stages[-1]
        assert processing_worker.stage_name == StageName.DATA_PROCESSING.value
        assert processing_worker.input_payload["retrieval"]["row_count"] == 2

        await service.run_worker(processing_worker.id)
        job = await approve(service)
        assert job.status == JobStatus.COMPLETED.value

    asyncio.run(scenario())
