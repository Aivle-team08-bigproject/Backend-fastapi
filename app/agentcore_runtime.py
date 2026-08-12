"""Bedrock AgentCore Runtime entrypoint.

이 앱은 공개 API가 아니라 AgentCore Runtime 내부에서만 호출된다. 인증은
AgentCore InvokeAgentRuntime IAM 정책 경계에서 처리하고, stage 결과 계약은
기존 AgentRuntimeClient의 결과를 그대로 반환한다.
"""

import asyncio
import logging
import time
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

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
_RUNTIME_FAILURE_CODE = {
    "requirement-analysis-agent": "REQUIRED_KEY_MISSING",
    "data-selection-agent": "SELECTION_RULE_INVALID",
    "data-processing-agent": "PROCESSING_RULE_INVALID",
}


@runtime_app.get("/ping")
async def ping() -> dict[str, str]:
    # AgentCore는 Healthy 상태의 세션을 idle 후보로 판단한다. 긴 LLM 호출 중에는
    # HealthyBusy를 반환해 Runtime이 처리 도중 교체되지 않도록 한다.
    return {"status": "HealthyBusy" if _active_invocations else "Healthy"}


@runtime_app.post("/invocations")
async def invoke(request: Request) -> JSONResponse:
    global _active_invocations

    body = await request.json()
    try:
        invocation = AgentCoreInvocationRequest.model_validate(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid invocation contract") from exc

    agent_name = invocation.agent_name
    model_name = invocation.model_name
    payload = invocation.payload

    reporter = None
    _active_invocations += 1
    started = time.monotonic()
    # 에이전트 진행 로그는 NeonDB로만 나간다. invocation이 424로 끊기면 DB 경로째
    # 사라지므로, 컨테이너가 어디까지 진행했는지는 stderr(CloudWatch Runtime 로그)에
    # 남은 이 경계 로그로만 판독할 수 있다.
    logger.info(
        "AgentCore invocation start: agent_name=%s execution_id=%s active=%d",
        agent_name,
        invocation.execution_id,
        _active_invocations,
    )
    try:
        # AgentCore가 내부 step/log를 NeonDB에 직접 기록한다. Redis publish는 하지 않으며,
        # FastAPI SSE가 PipelineEvent를 polling해 브라우저에 전달한다.
        reporter = AgentCoreStatusReporter(
            execution_id=invocation.execution_id,
            agent_name=agent_name,
            session_factory=runtime_database.session_factory,
            event_loop=asyncio.get_running_loop(),
        )
        # 상태 쓰기를 큐로 넘겨 에이전트 스레드가 DB 왕복을 기다리지 않게 한다.
        await reporter.start()
        client = AgentRuntimeClient(
            requirement_analysis_step_callback=reporter.requirement_analysis_step_callback,
            selection_step_callback=reporter.selection_step_callback,
            processing_step_callback=reporter.processing_step_callback,
            agent_log_callback=reporter.agent_log_callback,
            agent_session_factory=runtime_database.session_factory,
        )
        if invocation.step:
            # invocation 하나가 prompt step 하나만 담당한다. 68초 한도를 넘기지 않도록
            # 호출자가 세 번 나눠 부르고, 앞선 step 결과는 payload["prior"]로 온다.
            output = await client.run_selection_step(invocation.step, payload)
        else:
            output = await client.run(agent_name, model_name, payload)
        logger.info(
            "AgentCore agent finished: agent_name=%s execution_id=%s elapsed=%.1fs",
            agent_name,
            invocation.execution_id,
            time.monotonic() - started,
        )
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
        # Pydantic JSON mode와 JSONResponse 생성을 try 내부에서 끝낸다. 이전 구현은
        # FastAPI의 응답 직렬화가 try 바깥에서 실행되어 Decimal/datetime 같은 값이
        # 포함되면 제어되지 않은 HTTP 500으로 빠질 수 있었다.
        response_payload = AgentCoreInvocationResponse(output=output).model_dump(mode="json")
        logger.info(
            "AgentCore invocation returning: agent_name=%s execution_id=%s elapsed=%.1fs keys=%d",
            agent_name,
            invocation.execution_id,
            time.monotonic() - started,
            len(response_payload.get("output") or {}),
        )
        return JSONResponse(content=response_payload)
    except Exception as exc:  # noqa: BLE001 - Runtime caller receives a controlled failure payload
        # Managed Runtime logs can omit worker stderr. Persist the traceback in
        # the pipeline event stream so the invoking API and UI retain the real
        # failure cause.
        if reporter is not None:
            try:
                await reporter._record_log(  # noqa: SLF001 - Runtime-owned reporter
                    "ERROR",
                    "AgentCore Runtime 내부 실행 예외",
                    {"error_type": type(exc).__name__, "traceback": traceback.format_exc()},
                )
            except Exception:  # noqa: BLE001 - never hide the original Runtime error
                logger.exception("Unable to persist AgentCore Runtime failure")
        logger.exception(
            "Agent execution failed: agent_name=%s execution_id=%s elapsed=%.1fs",
            agent_name,
            invocation.execution_id,
            time.monotonic() - started,
        )
        # AgentCore의 HTTP 500은 호출자에게 RuntimeClientError만 남기고 실제 원인을
        # 버린다. 단계 실행 실패는 기존 AgentClient 계약의 _agent_error로 내려보내
        # FastAPI가 validation/failure_code 경로에서 DB와 화면에 정확히 기록하게 한다.
        failure_payload = AgentCoreInvocationResponse(
            output={
                "_agent_error": (
                    "AgentCore runtime execution failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
                "_failure_code": _RUNTIME_FAILURE_CODE.get(
                    agent_name, "SELECTION_RULE_INVALID"
                ),
            }
        ).model_dump(mode="json")
        return JSONResponse(content=failure_payload)
    finally:
        _active_invocations -= 1
        if reporter is not None:
            # 큐에 남은 진행 이벤트를 응답 반환 직전에 flush한다. 여기서 버리면
            # 실패 원인 분석에 필요한 마지막 상태가 사라진다.
            drain_started = time.monotonic()
            try:
                await reporter.drain()
            except Exception:  # noqa: BLE001 - flush 실패가 응답을 막으면 안 된다
                logger.exception("Unable to drain AgentCore status events")
            logger.info(
                "AgentCore status drain done: execution_id=%s elapsed=%.1fs total=%.1fs",
                invocation.execution_id,
                time.monotonic() - drain_started,
                time.monotonic() - started,
            )
