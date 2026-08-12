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


def test_agentcore_client_disables_sdk_level_retries(monkeypatch):
    monkeypatch.setattr(
        settings,
        "agentcore_runtime_arn",
        "arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:runtime/test",
    )
    captured = {}

    def fake_boto_client(_service, **kwargs):
        captured.update(kwargs)
        return FakeAgentCoreClient({})

    monkeypatch.setattr("boto3.client", fake_boto_client)
    AgentCoreRuntimeClient("exec-1")

    assert captured["config"].retries["total_max_attempts"] == 1


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


def test_runtime_session_id_is_stable_per_pipeline_run():
    """같은 run의 모든 stage가 한 AgentCore 세션을 공유해야 한다.

    2026-08-12 실측: stage마다 새 세션을 발급하면 앞 stage의 컨테이너가 살아 있는 채로
    새 세션 인스턴스 기동이 겹치고, 응답이 정상 반환돼도 호출자는 424를 받았다.
    """
    from app.domains.pipeline.agent_client import runtime_session_id

    first = runtime_session_id("bigproject", 42)
    second = runtime_session_id("bigproject", 42)
    other_run = runtime_session_id("bigproject", 43)

    assert first == second
    assert first != other_run
    # AgentCore runtimeSessionId는 33자 이상이어야 한다.
    assert len(first) >= 33


def test_runtime_session_id_falls_back_to_random_without_run_id():
    from app.domains.pipeline.agent_client import runtime_session_id

    assert runtime_session_id("bigproject", None) != runtime_session_id("bigproject", None)


def test_agentcore_client_uses_run_scoped_session(monkeypatch):
    monkeypatch.setattr(
        settings,
        "agentcore_runtime_arn",
        "arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:runtime/test",
    )
    fake = FakeAgentCoreClient(
        {"contentType": "application/json", "response": [b'{"output": {"ok": true}}']}
    )
    stage_one = AgentCoreRuntimeClient("exec-1", client=fake, pipeline_run_id=7)
    stage_two = AgentCoreRuntimeClient("exec-2", client=fake, pipeline_run_id=7)

    assert stage_one.runtime_session_id == stage_two.runtime_session_id

    asyncio.run(stage_one.run("requirement-analysis-agent", "", {}))
    assert fake.kwargs["runtimeSessionId"] == stage_one.runtime_session_id
