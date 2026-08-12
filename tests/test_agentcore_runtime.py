import asyncio
import json
from decimal import Decimal

from app import agentcore_runtime


class FakeRequest:
    async def json(self):
        return {
            "agent_name": "data-selection-agent",
            "model_name": "",
            "execution_id": "exec-runtime-test",
            "payload": {"raw_requirement": "test"},
        }


class FakeAgentClient:
    def __init__(self, **_kwargs):
        pass

    async def run(self, *_args):
        return {"amount": Decimal("12.50")}


class FailingAgentClient(FakeAgentClient):
    async def run(self, *_args):
        raise RuntimeError("late runtime failure")


def _prepare_runtime(monkeypatch, client_class):
    monkeypatch.setattr(agentcore_runtime, "AgentRuntimeClient", client_class)
    monkeypatch.setattr(
        agentcore_runtime.runtime_database,
        "_session_factory",
        lambda: None,
    )


def test_runtime_serializes_output_inside_controlled_boundary(monkeypatch):
    _prepare_runtime(monkeypatch, FakeAgentClient)

    response = asyncio.run(agentcore_runtime.invoke(FakeRequest()))

    assert response.status_code == 200
    assert json.loads(response.body) == {"output": {"amount": "12.50"}}


def test_runtime_returns_contract_failure_instead_of_http_500(monkeypatch):
    _prepare_runtime(monkeypatch, FailingAgentClient)

    response = asyncio.run(agentcore_runtime.invoke(FakeRequest()))
    body = json.loads(response.body)

    assert response.status_code == 200
    assert body["output"]["_failure_code"] == "SELECTION_RULE_INVALID"
    assert "RuntimeError: late runtime failure" in body["output"]["_agent_error"]
