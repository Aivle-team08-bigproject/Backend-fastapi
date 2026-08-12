"""Bedrock AgentCore Runtime entrypoint.

이 앱은 공개 API가 아니라 AgentCore Runtime 내부에서만 호출된다. 인증은
AgentCore InvokeAgentRuntime IAM 정책 경계에서 처리하고, stage 결과 계약은
기존 AgentRuntimeClient의 결과를 그대로 반환한다.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from app.agentcore_status_reporter import AgentCoreStatusReporter
from app.domains.pipeline.agent_client import AgentRuntimeClient
from app.domains.pipeline.agentcore_contract import (
    AgentCoreInvocationRequest,
    AgentCoreInvocationResponse,
)
from app.domains.pipeline.model import StageName
from app.domains.pipeline.validation import validate_stage_output
from agent_runtime.runtime_database import RuntimeDatabase
from agent_runtime.runtime_artifact_storage import store_final_csv_artifact

# Gunicorn owns the process logging configuration in AgentCore Runtime. Using
# its error logger guarantees exception tracebacks reach container stderr and
# therefore the managed CloudWatch runtime log stream.
logger = logging.getLogger("gunicorn.error")
runtime_database = RuntimeDatabase()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await runtime_database.initialize()
    try:
        yield
    finally:
        await runtime_database.dispose()


runtime_app = FastAPI(title="BigProject AgentCore Runtime", lifespan=lifespan)
_active_invocations = 0


@runtime_app.get("/ping")
async def ping() -> dict[str, str]:
    # AgentCore는 Healthy 상태의 세션을 idle 후보로 판단한다. 긴 LLM 호출 중에는
    # HealthyBusy를 반환해 Runtime이 처리 도중 교체되지 않도록 한다.
    return {"status": "HealthyBusy" if _active_invocations else "Healthy"}


@runtime_app.post("/invocations")
async def invoke(request: Request) -> dict:
    global _active_invocations

    body = await request.json()
    try:
        invocation = AgentCoreInvocationRequest.model_validate(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid invocation contract") from exc

    agent_name = invocation.agent_name
    model_name = invocation.model_name
    payload = invocation.payload

    _active_invocations += 1
    try:
        # AgentCore가 내부 step/log를 NeonDB에 직접 기록한다. Redis publish는 하지 않으며,
        # FastAPI SSE가 PipelineEvent를 polling해 브라우저에 전달한다.
        reporter = AgentCoreStatusReporter(
            execution_id=invocation.execution_id,
            agent_name=agent_name,
            session_factory=runtime_database.session_factory,
            event_loop=asyncio.get_running_loop(),
        )
        client = AgentRuntimeClient(
            requirement_analysis_step_callback=reporter.requirement_analysis_step_callback,
            selection_step_callback=reporter.selection_step_callback,
            processing_step_callback=reporter.processing_step_callback,
            agent_log_callback=reporter.agent_log_callback,
            agent_session_factory=runtime_database.session_factory,
        )
        output = await client.run(agent_name, model_name, payload)
        if agent_name == "data-processing-agent":
            # 유효하지 않은 산출물은 S3에 남기지 않는다. 유효한 CSV만 Runtime execution
            # role로 저장한 뒤, base64 대신 storage_key 메타데이터를 호출자에게 돌려준다.
            validation = validate_stage_output(StageName.DATA_PROCESSING, output)
            if validation["passed"]:
                context = payload.get("artifact_context")
                run_id = context.get("pipeline_run_id") if isinstance(context, dict) else None
                if not isinstance(run_id, int) or run_id <= 0:
                    raise ValueError("artifact_context.pipeline_run_id is required")
                output = await store_final_csv_artifact(output, run_id)
    except Exception as exc:  # noqa: BLE001 - Runtime caller receives a controlled 500
        logger.exception(
            "Agent execution failed: agent_name=%s execution_id=%s",
            agent_name,
            invocation.execution_id,
        )
        raise HTTPException(status_code=500, detail="agent execution failed") from exc
    finally:
        _active_invocations -= 1
    return AgentCoreInvocationResponse(output=output).model_dump()
