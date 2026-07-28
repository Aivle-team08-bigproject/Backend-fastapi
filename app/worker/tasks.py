import asyncio
import base64

from sqlalchemy import select

from agent_runtime.query import CsvQueryExecutor, DatabaseQueryExecutor
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.domains.pipeline.model import DataRequest, PipelineRun, PipelineRunStatus, StageRunStatus
from app.worker.celery_app import celery_app
from app.worker.status_publisher import publish_status
from app.worker.file_storage import read_csv_rows, write_result


async def _load_requirement(run_id: int) -> str:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(DataRequest.raw_requirement)
            .join(PipelineRun, PipelineRun.data_request_id == DataRequest.id)
            .where(PipelineRun.id == run_id)
        )
        raw_requirement = result.scalar_one_or_none()
        if raw_requirement is None:
            raise ValueError(f"pipeline run not found: {run_id}")
        return raw_requirement


async def _query_anonymized_database(selection: dict) -> list[dict]:
    from app.db.hanacard_agent_session import AsyncSessionLocal as AgentSessionLocal

    async with AgentSessionLocal() as db:
        return await DatabaseQueryExecutor(db).execute(selection)


@celery_app.task(bind=True, name="pipeline.process_run")
def process_pipeline_run(self, run_id: int, input_storage_key: str | None = None) -> dict:
    """요구사항 분석·데이터 선별 Agent를 Worker 프로세스에서 순차 실행한다."""
    celery_task_id = self.request.id
    current_stage = "REQUIREMENT_ANALYSIS"
    try:
        raw_requirement = asyncio.run(_load_requirement(run_id))
        publish_status(
            run_id=run_id,
            celery_task_id=celery_task_id,
            run_status=PipelineRunStatus.RUNNING,
            current_stage=current_stage,
            stage_status=StageRunStatus.RUNNING,
            progress_percent=5,
            message="요구사항 분석을 시작했습니다.",
        )

        from agent_runtime.requirements_analysis.agent import run as run_requirement_analysis

        analysis_result = run_requirement_analysis(raw_requirement)
        if not analysis_result.get("ok"):
            raise RuntimeError(analysis_result.get("error_message") or "요구사항 분석에 실패했습니다.")
        analysis = analysis_result["data"]
        publish_status(
            run_id=run_id,
            celery_task_id=celery_task_id,
            run_status=PipelineRunStatus.RUNNING,
            current_stage=current_stage,
            stage_status=StageRunStatus.COMPLETED,
            progress_percent=33,
            message="요구사항 분석이 완료되었습니다.",
            result=analysis,
        )

        current_stage = "DATA_SELECTION"
        publish_status(
            run_id=run_id,
            celery_task_id=celery_task_id,
            run_status=PipelineRunStatus.RUNNING,
            current_stage=current_stage,
            stage_status=StageRunStatus.RUNNING,
            progress_percent=40,
            message="데이터 선별을 시작했습니다.",
        )

        from agent_runtime.data_selection.agent import run as run_data_selection

        selection_result = run_data_selection(
            raw_requirement,
            {
                "usage_purpose": analysis.get("usage_purpose"),
                "requested_data_sentence": analysis.get("requested_data_summary"),
                "categories": analysis.get("requested_data_categories", {}),
                "delivery_channel": analysis.get("delivery_channel"),
                "output_formats": analysis.get("output_format", []),
            },
            ["merchant", "member_pseudonymized", "transaction_pseudonymized"],
        )
        if not selection_result.get("ok"):
            raise RuntimeError(selection_result.get("error_message") or "데이터 선별에 실패했습니다.")
        selection = selection_result["data"]
        publish_status(
            run_id=run_id,
            celery_task_id=celery_task_id,
            run_status=PipelineRunStatus.RUNNING,
            current_stage=current_stage,
            stage_status=StageRunStatus.COMPLETED,
            progress_percent=66,
            message="데이터 선별이 완료되었습니다.",
            result=selection,
        )

        if input_storage_key is None and settings.pipeline_query_source.lower() != "database":
            publish_status(
                run_id=run_id,
                celery_task_id=celery_task_id,
                run_status=PipelineRunStatus.WAITING_SAMPLE_REVIEW,
                current_stage="DATA_PROCESSING",
                progress_percent=66,
                message="CSV 입력을 기다리고 있습니다.",
            )
            return {"run_id": run_id, "analysis": analysis, "selection": selection}

        current_stage = "DATA_PROCESSING"
        publish_status(
            run_id=run_id,
            celery_task_id=celery_task_id,
            run_status=PipelineRunStatus.RUNNING,
            current_stage=current_stage,
            stage_status=StageRunStatus.RUNNING,
            progress_percent=70,
            message="업로드된 CSV 가공을 시작했습니다.",
        )
        if input_storage_key is not None:
            rows = CsvQueryExecutor().execute(read_csv_rows(input_storage_key), selection)
            query_source = "csv"
        else:
            rows = asyncio.run(_query_anonymized_database(selection))
            query_source = "database"
        if not rows:
            raise RuntimeError("선별 조건에 일치하는 데이터가 없습니다.")
        from agent_runtime.data_processing.processor import process_payload

        processing = process_payload(
            {
                "raw_requirement": raw_requirement,
                "analysis": analysis,
                "selection": selection,
                "selected_rows": rows,
            }
        )
        csv_artifact = processing.get("csv_artifact")
        if not csv_artifact:
            raise RuntimeError("CSV 결과가 생성되지 않았습니다.")
        artifact = write_result(
            run_id,
            base64.b64decode(csv_artifact["content_base64"], validate=True),
        )
        processing_summary = {
            "processing_engine": processing.get("processing_engine"),
            "query_source": query_source,
            "selected_row_count": len(rows),
            "processed_columns": processing.get("processed_columns", []),
            "processing_explanation": processing.get("processing_explanation", {}),
            "quality_report": processing.get("quality_report", {}),
        }
        result = {"processing": processing_summary, "artifact": artifact}
        publish_status(
            run_id=run_id,
            celery_task_id=celery_task_id,
            run_status=PipelineRunStatus.COMPLETED,
            current_stage=current_stage,
            stage_status=StageRunStatus.COMPLETED,
            progress_percent=100,
            message="CSV 가공과 결과 파일 저장이 완료되었습니다.",
            result=result,
        )
        return {"run_id": run_id, **result}
    except Exception as exc:
        publish_status(
            run_id=run_id,
            celery_task_id=celery_task_id,
            run_status=PipelineRunStatus.FAILED,
            current_stage=current_stage,
            stage_status=StageRunStatus.FAILED,
            progress_percent=0,
            message=f"{current_stage} 실행에 실패했습니다.",
            error_message=str(exc),
        )
        raise
