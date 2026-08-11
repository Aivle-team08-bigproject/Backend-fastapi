import pytest
from pydantic import ValidationError

from app.domains.pipeline.agentcore_contract import (
    AgentCoreErrorResponse,
    AgentCoreInvocationRequest,
    AgentCoreInvocationResponse,
)


def test_invocation_request_has_stable_correlation_fields():
    request = AgentCoreInvocationRequest(
        agent_name="data-selection-agent",
        execution_id="execution-123",
        payload={"raw_requirement": "매출"},
    )

    assert request.execution_id == "execution-123"
    assert request.model_name == ""


def test_invocation_request_rejects_unknown_agent_and_extra_fields():
    with pytest.raises(ValidationError):
        AgentCoreInvocationRequest(
            agent_name="unknown-agent",
            execution_id="execution-123",
            payload={},
        )

    with pytest.raises(ValidationError):
        AgentCoreInvocationRequest(
            agent_name="data-selection-agent",
            execution_id="execution-123",
            payload={},
            secret="must-not-cross-boundary",
        )


def test_response_and_error_contracts_are_strict():
    assert AgentCoreInvocationResponse.model_validate({"output": {"ok": True}}).output == {
        "ok": True
    }
    error = AgentCoreErrorResponse(
        error="agent_execution_failed",
        retryable=True,
        message="agent execution failed",
    )
    assert error.retryable is True

    with pytest.raises(ValidationError):
        AgentCoreInvocationResponse.model_validate({"result": {"ok": True}})
