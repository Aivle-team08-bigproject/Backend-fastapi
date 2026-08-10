import asyncio
import json

import pytest

from app.core.config import settings
from app.domains.pipeline.agent_client import (
    AgentCoreInvocationError,
    AgentCoreRuntimeClient,
)


class FakeAgentCoreClient:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def invoke_agent_runtime(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def test_agentcore_client_invokes_runtime_and_decodes_json(monkeypatch):
    monkeypatch.setattr(
        settings,
        "agentcore_runtime_arn",
        "arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:runtime/test",
    )
    fake = FakeAgentCoreClient(
        {"contentType": "application/json", "response": [b'{"output": {"ok": true}}']}
    )
    client = AgentCoreRuntimeClient("exec-1", client=fake)

    result = asyncio.run(client.run("requirement-analysis-agent", "", {"value": "x"}))

    assert result == {"ok": True}
    assert fake.kwargs["agentRuntimeArn"].endswith("runtime/test")
    request = json.loads(fake.kwargs["payload"])
    assert request["execution_id"] == "exec-1"
    assert request["payload"] == {"value": "x"}


def test_agentcore_client_requires_runtime_arn(monkeypatch):
    monkeypatch.setattr(settings, "agentcore_runtime_arn", None)

    with pytest.raises(AgentCoreInvocationError, match="AGENTCORE_RUNTIME_ARN"):
        AgentCoreRuntimeClient("exec-1", client=FakeAgentCoreClient({}))


@pytest.mark.parametrize("agent_name", ["data-selection-agent", "data-processing-agent"])
def test_agentcore_client_delegates_db_stages_to_runtime(monkeypatch, agent_name):
    monkeypatch.setattr(
        settings,
        "agentcore_runtime_arn",
        "arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:runtime/test",
    )
    fake = FakeAgentCoreClient(
        {"contentType": "application/json", "response": [b'{"output": {"ok": true}}']}
    )

    result = asyncio.run(AgentCoreRuntimeClient("exec-1", client=fake).run(agent_name, "", {"x": 1}))

    assert result == {"ok": True}
    request = json.loads(fake.kwargs["payload"])
    assert request["agent_name"] == agent_name
    assert request["payload"] == {"x": 1}
