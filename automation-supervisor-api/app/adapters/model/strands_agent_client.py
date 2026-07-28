import asyncio
import json
import sys
from pathlib import Path

from app.adapters.model.stub_agent_client import StubAgentClient
from app.agents.strands_agent_factory import build_strands_agent, parse_agent_json_response
from app.application.ports.agent_client import AgentClient
from app.core.config import settings

# agent_runtime/은 이 서비스(automation-supervisor-api/)와 별개로 저장소 루트에 있는 독립
# 모듈이다 — 이 서비스의 기본 실행 방식(cwd=automation-supervisor-api/, 자체 venv)에서는
# sys.path에 안 잡혀서 import가 안 된다. 로컬에서 실제 요구사항 분석 에이전트로 통합 테스트를
# 하기 위해 저장소 루트를 경로에 추가해준다.
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


class StrandsAgentClient(AgentClient):
    def __init__(self, fallback_to_stub: bool = True):
        self.fallback_to_stub = fallback_to_stub
        self.stub = StubAgentClient()

    async def run(self, agent_name: str, model_name: str, payload: dict) -> dict:
        if agent_name == "requirement-analysis-agent":
            return await self._run_requirement_analysis(payload)
        if agent_name == "data-selection-agent":
            return await self._run_data_selection(payload)
        if agent_name == "data-processing-agent":
            return await self._run_data_processing(payload)

        agent = build_strands_agent(agent_name, model_name)
        if agent is None:
            if not self.fallback_to_stub:
                raise RuntimeError("strands-agents is not installed")
            return await self.stub.run(agent_name, model_name, payload)

        response = agent(json.dumps(payload, ensure_ascii=False))
        return parse_agent_json_response(response)

    async def _run_requirement_analysis(self, payload: dict) -> dict:
        """실제 요구사항 분석 에이전트(agent_runtime/requirements_analysis)를 호출한다.

        이 에이전트는 자기 모델(DeepSeek)을 스스로 선택하므로 model_name 인자는 여기서
        쓰지 않는다 — settings.requirement_analysis_model("sonnet-4.6")은 나중에 실제 API를
        바꿀 때 agent_runtime 쪽 설정으로 반영하면 된다.

        필드명이 이 서비스의 검증 스키마(validation.py)와 다르다
        (requested_data_summary -> requested_data_sentence,
         requested_data_categories -> categories, output_format -> output_formats)
        — 그래서 여기서 변환한다. agent_runtime 쪽 계약 자체는 안 바꾼다(그쪽 FastAPI
        테스트 하니스가 이미 그 필드명을 쓰고 있어서).

        supervisor_service.run_stage()가 이 메서드 호출을 try/except로 감싸지 않으므로,
        여기서 예외를 던지면 정상적인 실패 처리(검증 실패 -> FAILED + failure_code + 롤백
        판단)를 못 타고 처리 안 된 예외로 터진다. 그래서 실패 시에도 예외 대신 필수 키가
        빠진 dict를 반환한다 — validate_stage_output이 이걸 REQUIRED_KEY_MISSING으로
        정상 처리해준다.
        """
        from agent_runtime.requirements_analysis.agent import run as run_requirements_analysis

        raw_requirement = payload["raw_requirement"]
        result = await asyncio.to_thread(run_requirements_analysis, raw_requirement)

        if not result["ok"]:
            return {"_agent_error": result["error_message"] or "requirement analysis agent failed"}

        data = result["data"]
        return {
            "usage_purpose": data["usage_purpose"],
            "requested_data_sentence": data["requested_data_summary"],
            "categories": data["requested_data_categories"],
            "delivery_channel": data["delivery_channel"],
            "output_formats": data["output_format"],
        }

    async def _run_data_selection(self, payload: dict) -> dict:
        """실제 데이터 선별 에이전트(agent_runtime/data_selection)를 호출한다.

        supervisor_service.py가 만드는 payload 계약은 {raw_requirement, analysis, available_data}
        이다. agent_runtime 쪽 산출물 필드명(selected_tables, selection_query)이 이미
        validation.py가 요구하는 이름과 같아서, requirement-analysis-agent와 달리 필드명
        변환이 필요 없다.

        실패 시에도 예외 대신 필수 키가 빠진 dict를 반환한다 — validate_stage_output이 이걸
        REQUIRED_KEY_MISSING으로 정상 처리해준다(_run_requirement_analysis와 동일한 이유).
        """
        from agent_runtime.data_selection.agent import run as run_data_selection

        result = await asyncio.to_thread(
            run_data_selection,
            payload["raw_requirement"],
            payload.get("analysis", {}),
            payload.get("available_data", []),
        )

        if not result["ok"]:
            return {"_agent_error": result["error_message"] or "data selection agent failed"}

        return result["data"]

    async def _run_data_processing(self, payload: dict) -> dict:
        """Call the deterministic data-processing Strands tool.

        The query layer must attach actual rows to ``selected_rows`` (or
        ``selection.selected_rows``) before this stage runs.
        """
        if not payload.get("selected_rows") and settings.pipeline_query_source.lower() == "database":
            try:
                from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

                from agent_runtime.query import DatabaseQueryExecutor

                engine = create_async_engine(settings.portfolio_agent_database_url, pool_pre_ping=True)
                try:
                    async with AsyncSession(engine) as session:
                        payload = {
                            **payload,
                            "selected_rows": await DatabaseQueryExecutor(session).execute(
                                payload.get("selection") or {}
                            ),
                        }
                finally:
                    await engine.dispose()
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
