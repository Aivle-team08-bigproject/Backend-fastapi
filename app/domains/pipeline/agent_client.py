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
            return {
                "_agent_error": f"database metadata lookup failed: {exc}",
                "_failure_code": "INSUFFICIENT_DATA",
            }
        if not schema_metadata:
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

            async with AgentSessionLocal() as db:
                payload = {
                    **payload,
                    "processing_plan": processing_plan,
                    "processing_agent": planning_audit,
                    "selected_rows": await DatabaseQueryExecutor(db).execute(
                        payload.get("selection") or {}
                    ),
                }
        except PrivacyThresholdError as exc:
            return {
                "_agent_error": str(exc),
                "_failure_code": "PRIVACY_THRESHOLD_NOT_MET",
            }
        except Exception as exc:
            return {
                "_agent_error": f"query layer failed: {exc}",
                "_failure_code": "INSUFFICIENT_DATA",
            }

        from agent_runtime.data_processing.agent import run as run_data_processing

        result = await asyncio.to_thread(run_data_processing, payload)
        if not result["ok"]:
            return {
                "_agent_error": result["error_message"] or "data processing agent failed",
                "_failure_code": result.get("failure_code", "PROCESSING_RULE_INVALID"),
            }
        return result["data"]
