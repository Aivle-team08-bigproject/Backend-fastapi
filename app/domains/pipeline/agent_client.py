"""Supervisor가 각 단계 에이전트를 호출할 때 쓰는 어댑터.

에이전트 구현체는 저장소 루트의 agent_runtime/에 있다 — 이 앱과 같은 sys.path에 있으므로
바로 import한다(automation-supervisor-api/ 시절에는 cwd가 달라서 저장소 루트를 sys.path에
끼워넣는 우회가 필요했지만, 여기서는 필요 없다).

각 _run_* 메서드는 실패 시에도 예외를 던지지 않고 필수 키가 빠진 dict를 돌려준다 —
validation.validate_stage_output이 그걸 REQUIRED_KEY_MISSING 등으로 정상 처리해서
"검증 실패 -> FAILED + failure_code" 경로를 타게 하기 위한 것이다. 예외로 터지면 그 경로를
못 탄다.
"""

import asyncio
from collections.abc import Callable
from typing import Protocol

AnalysisStepCallback = Callable[[str, str, dict | None], None]
SelectionStepCallback = Callable[[str, str, dict | None], None]
ProcessingStepCallback = Callable[[str, str, dict | None], None]
# (level, message, detail) — 단계 상태가 아니라 에이전트 내부 관찰 기록을 흘려보낸다.
AgentLogCallback = Callable[[str, str, dict | None], None]


class AgentClient(Protocol):
    async def run(self, agent_name: str, model_name: str, payload: dict) -> dict:
        ...


class AgentRuntimeClient(AgentClient):
    """agent_runtime/의 실제 에이전트를 호출한다."""

    def __init__(
        self,
        requirement_analysis_step_callback: AnalysisStepCallback | None = None,
        selection_step_callback: SelectionStepCallback | None = None,
        processing_step_callback: ProcessingStepCallback | None = None,
        agent_log_callback: AgentLogCallback | None = None,
    ):
        self.requirement_analysis_step_callback = requirement_analysis_step_callback
        self.selection_step_callback = selection_step_callback
        self.processing_step_callback = processing_step_callback
        self.agent_log_callback = agent_log_callback

    async def _log(self, level: str, message: str, detail: dict | None = None) -> None:
        """기술 로그를 화면으로 보낸다. 콜백이 없으면(테스트 등) 조용히 넘어간다.

        에이전트 재시도 루프 안쪽은 각 agent_runtime 모듈이 직접 로깅하고, 여기서는
        그 바깥 — DB 메타데이터 조회·query layer·executor처럼 LLM이 아닌 실행 단계를
        기록한다. 실무 실패는 오히려 이쪽에서 자주 난다.

        agent_log_callback은 Worker 이벤트 루프에 코루틴을 예약한 뒤 완료를 기다리는
        동기 callback이다. 여기서 이벤트 루프가 직접 호출하면 자기 자신이 완료되기를
        기다리는 deadlock이 생기므로, 반드시 별도 스레드에서 호출한다.
        """
        if self.agent_log_callback is None:
            return
        await asyncio.to_thread(self.agent_log_callback, level, message, detail)

    async def _processing_step(
        self, step_code: str, status: str, metadata: dict | None = None
    ) -> None:
        """이벤트 루프에서 실행되는 가공 단계를 안전하게 체크리스트에 기록한다."""
        if self.processing_step_callback is None:
            return
        await asyncio.to_thread(
            self.processing_step_callback, step_code, status, metadata
        )

    async def run(self, agent_name: str, model_name: str, payload: dict) -> dict:
        if agent_name == "requirement-analysis-agent":
            return await self._run_requirement_analysis(payload)
        if agent_name == "data-selection-agent":
            return await self._run_data_selection(payload)
        if agent_name == "data-processing-agent":
            return await self._run_data_processing(payload)
        raise ValueError(f"unknown agent: {agent_name}")

    async def _run_requirement_analysis(self, payload: dict) -> dict:
        """요구사항 분석 에이전트 호출.

        이 에이전트는 자기 모델을 스스로 선택하므로 model_name은 쓰지 않는다.
        agent_runtime 쪽 필드명이 validation.py의 검증 스키마와 달라서 여기서 변환한다
        (requested_data_summary -> requested_data_sentence, requested_data_categories ->
        categories, output_format -> output_formats). agent_runtime 쪽 계약은 안 바꾼다.

        내부적으로 요청 분석 -> 요청 구조화 -> 데이터 범주화 3단계를 독립 프롬프트로 순차
        실행하며, 각 단계 경계마다 requirement_analysis_step_callback으로 진행 상태를
        알린다 — data-selection/data-processing과 동일한 패턴이다. run_steps()가 던지는
        예외는 여기서 잡지 않고 그대로 전파한다(_run_data_selection과 동일) — supervisor.py의
        공통 예외 처리 경로가 "검증 실패 -> FAILED + failure_code"로 처리한다.
        """
        from agent_runtime.requirements_analysis.agent import run_steps as run_requirements_analysis_steps

        data = await asyncio.to_thread(
            run_requirements_analysis_steps,
            payload["raw_requirement"],
            self.requirement_analysis_step_callback,
            self.agent_log_callback,
        )
        return {
            "usage_purpose": data["usage_purpose"],
            "requested_data_sentence": data["requested_data_summary"],
            "categories": data["requested_data_categories"],
            "delivery_channel": data["delivery_channel"],
            "output_formats": data["output_format"],
        }

    async def _run_data_selection(self, payload: dict) -> dict:
        """Neon DB COMMENT 메타데이터를 읽어 컬럼 설계 에이전트에 전달한다."""
        from agent_runtime.data_selection.agent import run_steps as run_data_selection_steps
        from agent_runtime.query.metadata import (
            load_dataset_metadata,
            load_reference_catalogs,
        )
        from app.db.hanacard_agent_session import AsyncSessionLocal as AgentSessionLocal

        available_data = payload.get("available_data", [])
        try:
            async with AgentSessionLocal() as db:
                schema_metadata = await load_dataset_metadata(db, available_data)
                reference_catalogs = await load_reference_catalogs(db)
        except Exception as exc:
            await self._log("ERROR", f"DB 메타데이터 조회 실패: {exc}", {"phase": "metadata"})
            return {
                "_agent_error": f"database metadata lookup failed: {exc}",
                "_failure_code": "INSUFFICIENT_DATA",
            }
        if not schema_metadata:
            await self._log(
                "ERROR",
                "접근 가능한 컬럼이 없어 선별을 시작할 수 없습니다 — 계정 권한이나 "
                "요청 데이터 범위를 확인해야 합니다.",
                {"phase": "metadata", "available_data": available_data},
            )
            return {
                "_agent_error": "database metadata lookup returned no accessible columns",
                "_failure_code": "INSUFFICIENT_DATA",
            }

        try:
            return await asyncio.to_thread(
                run_data_selection_steps,
                payload["raw_requirement"],
                payload.get("analysis", {}),
                available_data,
                schema_metadata,
                payload.get("hitl_feedback"),
                reference_catalogs,
                self.selection_step_callback,
                self.agent_log_callback,
            )
        except Exception as exc:
            failure = {
                "_agent_error": f"selection agent failed: {exc}",
                "_failure_code": "SELECTION_RULE_INVALID",
            }
            failure_snapshot = getattr(exc, "failure_snapshot", None)
            if isinstance(failure_snapshot, dict):
                failure["failure_snapshot"] = failure_snapshot
            else:
                # failure_snapshot이 있으면 단계별 재시도 루프가 이미 사유를 로깅했다.
                # 없는 경우는 세 단계를 다 통과한 뒤 최종 계약 검증에서 터진 것이라
                # 여기서 남기지 않으면 아무 데도 안 남는다.
                await self._log("ERROR", f"선별 결과 최종 검증 실패: {exc}", {"phase": "contract"})
            return failure

    async def _run_data_processing(self, payload: dict) -> dict:
        """LLM이 가공 계획을 설계한 뒤 실제 행은 결정론적 executor로만 처리한다."""
        try:
            from agent_runtime.data_processing.config import settings as processing_settings
            from agent_runtime.data_processing.planning_agent import create_processing_plan

            processing_plan = await asyncio.to_thread(
                create_processing_plan,
                payload,
                self.processing_step_callback,
                self.agent_log_callback,
            )
            planning_audit = {
                "provider": processing_settings.data_processing_model_provider,
                "model_id": processing_settings.data_processing_model_id,
            }
        except Exception as exc:
            await self._log("ERROR", f"가공 계획 수립 실패: {exc}", {"phase": "planning"})
            failure = {
                "_agent_error": f"processing plan agent failed: {exc}",
                "_failure_code": "PROCESSING_RULE_INVALID",
            }
            failure_snapshot = getattr(exc, "failure_snapshot", None)
            if isinstance(failure_snapshot, dict):
                failure["failure_snapshot"] = failure_snapshot
            return failure

        try:
            from app.db.hanacard_agent_session import AsyncSessionLocal as AgentSessionLocal

            from agent_runtime.query import DatabaseQueryExecutor, PrivacyThresholdError

            await self._processing_step("SOURCE_DATA_RETRIEVAL", "RUNNING")
            await self._log("INFO", "승인된 선별 계획으로 원천 데이터를 조회합니다.", {"phase": "query"})
            async with AgentSessionLocal() as db:
                selected_rows = await DatabaseQueryExecutor(db).execute(
                    payload.get("selection") or {}
                )
                payload = {
                    **payload,
                    "processing_plan": processing_plan,
                    "processing_agent": planning_audit,
                    "selected_rows": selected_rows,
                }
            await self._log(
                "INFO",
                f"원천 데이터 조회 완료: {len(selected_rows)}행",
                {"phase": "query", "row_count": len(selected_rows)},
            )
            await self._processing_step(
                "SOURCE_DATA_RETRIEVAL", "COMPLETED", {"row_count": len(selected_rows)}
            )
        except PrivacyThresholdError as exc:
            # 프라이버시 임계 미달은 설정 실수가 아니라 정책상 정상 차단이다 — 실무자가
            # 조건을 어떻게 바꿔야 하는지 알 수 있게 사유를 그대로 보여준다.
            await self._log("ERROR", f"프라이버시 기준 미달로 조회가 차단되었습니다: {exc}", {"phase": "query"})
            await self._processing_step(
                "SOURCE_DATA_RETRIEVAL", "FAILED", {"validation_errors": [str(exc)]}
            )
            return {
                "_agent_error": str(exc),
                "_failure_code": "PRIVACY_THRESHOLD_NOT_MET",
            }
        except Exception as exc:
            await self._log("ERROR", f"원천 데이터 조회 실패: {exc}", {"phase": "query"})
            await self._processing_step(
                "SOURCE_DATA_RETRIEVAL", "FAILED", {"validation_errors": [str(exc)]}
            )
            return {
                "_agent_error": f"query layer failed: {exc}",
                "_failure_code": "INSUFFICIENT_DATA",
            }

        from agent_runtime.data_processing.agent import run as run_data_processing

        await self._processing_step("DETERMINISTIC_PROCESSING", "RUNNING")
        await self._log("INFO", "가공 계획을 실제 데이터에 적용합니다.", {"phase": "execute"})
        try:
            result = await asyncio.to_thread(run_data_processing, payload)
        except Exception as exc:  # noqa: BLE001 - 실행기 예외도 checklist에 실패로 남긴다
            error_message = f"data processing executor failed: {exc}"
            await self._log("ERROR", f"가공 실행 실패: {error_message}", {"phase": "execute"})
            await self._processing_step(
                "DETERMINISTIC_PROCESSING", "FAILED", {"validation_errors": [error_message]}
            )
            return {
                "_agent_error": error_message,
                "_failure_code": "PROCESSING_RULE_INVALID",
            }
        if not result["ok"]:
            error_message = result["error_message"] or "data processing agent failed"
            await self._log("ERROR", f"가공 실행 실패: {error_message}", {"phase": "execute"})
            await self._processing_step(
                "DETERMINISTIC_PROCESSING", "FAILED", {"validation_errors": [error_message]}
            )
            return {
                "_agent_error": error_message,
                "_failure_code": result.get("failure_code", "PROCESSING_RULE_INVALID"),
            }
        quality_report = result["data"].get("quality_report") or {}
        await self._processing_step(
            "DETERMINISTIC_PROCESSING",
            "COMPLETED",
            {"output_row_count": quality_report.get("output_row_count")},
        )
        return result["data"]
