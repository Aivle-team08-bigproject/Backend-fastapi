"""Bedrock AgentCore Runtime entrypoint.

이 앱은 공개 API가 아니라 AgentCore Runtime 내부에서만 호출된다. 인증은
AgentCore InvokeAgentRuntime IAM 정책 경계에서 처리하고, stage 결과 계약은
기존 AgentRuntimeClient의 결과를 그대로 반환한다.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from app.domains.pipeline.agent_client import AgentRuntimeClient
from agent_runtime.runtime_database import RuntimeDatabase

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
    agent_name = body.get("agent_name")
    model_name = body.get("model_name", "")
    payload = body.get("payload")
    if not isinstance(agent_name, str) or not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="agent_name and payload are required")

    _active_invocations += 1
    try:
        client = AgentRuntimeClient(agent_session_factory=runtime_database.session_factory)
        output = await client.run(agent_name, model_name, payload)
    except Exception as exc:  # noqa: BLE001 - Runtime caller receives a controlled 500
        logger.exception(
            "Agent execution failed: agent_name=%s execution_id=%s",
            agent_name,
            payload.get("execution_id"),
        )
        raise HTTPException(status_code=500, detail="agent execution failed") from exc
    finally:
        _active_invocations -= 1
    return {"output": output}
