import asyncio
import json

import pytest

from app.core.config import settings
from app.domains.pipeline.agent_client import (
    AgentCoreInvocationError,
    AgentCoreRuntimeClient,
)


async def _no_sleep(_seconds):
    return None


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
    # 데이터 선별은 prompt step 단위 invocation으로 나뉘므로 앞선 step 결과가 prior로 실린다.
    assert request["payload"]["x"] == 1


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


class ConflictThenSuccessClient:
    """첫 호출은 세션 프로비저닝 경합으로 거절하고 두 번째에 성공한다."""

    def __init__(self, response, failures=1, code="RetryableConflictException"):
        self.response = response
        self.remaining = failures
        self.code = code
        self.calls = 0
        self.kwargs = None

    def invoke_agent_runtime(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        if self.remaining > 0:
            self.remaining -= 1
            error = Exception("session operation in progress")
            error.response = {
                "Error": {"Code": self.code, "Message": "Session operation in progress"},
                "ResponseMetadata": {"HTTPStatusCode": 409},
            }
            raise error
        return self.response


def _arn(monkeypatch):
    monkeypatch.setattr(
        settings,
        "agentcore_runtime_arn",
        "arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:runtime/test",
    )


def test_retries_session_provisioning_conflict(monkeypatch):
    """409는 요청이 에이전트에 도달하기 전 오류라 재시도해도 중복 실행이 없다."""
    _arn(monkeypatch)
    monkeypatch.setattr("app.domains.pipeline.agent_client.asyncio.sleep", _no_sleep)
    fake = ConflictThenSuccessClient(
        {"contentType": "application/json", "response": [b'{"output": {"ok": true}}']}
    )
    client = AgentCoreRuntimeClient("exec-1", client=fake, pipeline_run_id=7)

    assert asyncio.run(client.run("requirement-analysis-agent", "", {})) == {"ok": True}
    assert fake.calls == 2


def test_does_not_retry_runtime_client_error(monkeypatch):
    """424는 에이전트가 이미 실행을 마친 뒤일 수 있어 재시도하면 단계가 중복 실행된다."""
    _arn(monkeypatch)

    class AlwaysRuntimeClientError:
        def __init__(self):
            self.calls = 0

        def invoke_agent_runtime(self, **kwargs):
            self.calls += 1
            error = Exception("Received error (424) from runtime")
            error.response = {
                "Error": {"Code": "RuntimeClientError", "Message": "runtime error"},
                "ResponseMetadata": {"HTTPStatusCode": 424},
            }
            raise error

    fake = AlwaysRuntimeClientError()
    client = AgentCoreRuntimeClient("exec-1", client=fake, pipeline_run_id=7)

    with pytest.raises(AgentCoreInvocationError):
        asyncio.run(client.run("requirement-analysis-agent", "", {}))
    assert fake.calls == 1


def test_gives_up_after_repeated_conflicts(monkeypatch):
    _arn(monkeypatch)
    monkeypatch.setattr("app.domains.pipeline.agent_client.asyncio.sleep", _no_sleep)
    fake = ConflictThenSuccessClient({}, failures=99)
    client = AgentCoreRuntimeClient("exec-1", client=fake, pipeline_run_id=7)

    with pytest.raises(AgentCoreInvocationError):
        asyncio.run(client.run("requirement-analysis-agent", "", {}))
    assert fake.calls == 3


def test_decodes_multibyte_json_split_across_chunks(monkeypatch):
    """botocore StreamingBody는 1024바이트로 끊는다. 한글이 경계에 걸려도 깨지면 안 된다.

    2026-08-12 실측: run 2/3/6/7의 실패가 424가 아니라 이 경로의
    UnicodeDecodeError('utf-8' codec can't decode bytes in position 1022-1023)였다.
    """
    _arn(monkeypatch)
    payload = json.dumps(
        {"output": {"reason": "가" * 800, "ok": True}}, ensure_ascii=False
    ).encode("utf-8")
    # 1024바이트 경계에서 자른다. 한글이 3바이트라 대부분 글자 중간에서 잘린다.
    chunks = [payload[i : i + 1024] for i in range(0, len(payload), 1024)]
    assert len(chunks) > 1
    fake = FakeAgentCoreClient({"contentType": "application/json", "response": chunks})
    client = AgentCoreRuntimeClient("exec-1", client=fake, pipeline_run_id=7)

    result = asyncio.run(client.run("data-selection-agent", "", {}))

    assert result["ok"] is True
    assert result["reason"] == "가" * 800


def test_invalid_json_still_raises_contract_error(monkeypatch):
    _arn(monkeypatch)
    fake = FakeAgentCoreClient(
        {"contentType": "application/json", "response": [b"{not json"]}
    )
    client = AgentCoreRuntimeClient("exec-1", client=fake, pipeline_run_id=7)

    with pytest.raises(AgentCoreInvocationError, match="JSON response is invalid"):
        asyncio.run(client.run("data-selection-agent", "", {}))


class RecordingStepClient:
    """step별 부분 결과를 돌려주며 요청을 기록한다."""

    def __init__(self):
        self.requests = []
        self._outputs = {
            "SOURCE_COLUMN_SELECTION": {"source_columns": [{"column": "age_band"}]},
            "DERIVED_COLUMN_DESIGN": {"derived_columns": [{"name": "d1"}]},
            "SYNTHETIC_SAMPLE_GENERATION": {"sample_rows": [{"age_band": "20대"}]},
        }

    def invoke_agent_runtime(self, **kwargs):
        request = json.loads(kwargs["payload"])
        self.requests.append(request)
        body = json.dumps({"output": self._outputs[request["step"]]}).encode("utf-8")
        return {"contentType": "application/json", "response": [body]}


def test_data_selection_is_split_into_three_step_invocations(monkeypatch):
    """AgentCore는 68초 근처에서 invocation을 끊는다. step마다 나눠 호출해야 한다."""
    _arn(monkeypatch)
    fake = RecordingStepClient()
    client = AgentCoreRuntimeClient("exec-1", client=fake, pipeline_run_id=7)

    result = asyncio.run(client.run("data-selection-agent", "", {"raw_requirement": "요청"}))

    assert [r["step"] for r in fake.requests] == [
        "SOURCE_COLUMN_SELECTION",
        "DERIVED_COLUMN_DESIGN",
        "SYNTHETIC_SAMPLE_GENERATION",
    ]
    # 앞선 step 결과가 다음 invocation에 누적되어 전달된다.
    assert fake.requests[0]["payload"]["prior"] == {}
    assert "source_columns" in fake.requests[1]["payload"]["prior"]
    assert "derived_columns" in fake.requests[2]["payload"]["prior"]
    # 세 step 모두 같은 세션으로 라우팅된다.
    assert len({r["execution_id"] for r in fake.requests}) == 1
    # 호출자에게는 기존과 동일한 병합 결과가 돌아간다.
    assert set(result) == {"source_columns", "derived_columns", "sample_rows"}


def test_selection_step_failure_stops_remaining_invocations(monkeypatch):
    """통제된 실패는 뒤 step을 실행하지 않고 그대로 전달한다."""
    _arn(monkeypatch)

    class FailingStepClient:
        def __init__(self):
            self.calls = 0

        def invoke_agent_runtime(self, **kwargs):
            self.calls += 1
            body = json.dumps(
                {"output": {"_agent_error": "boom", "_failure_code": "SELECTION_RULE_INVALID"}}
            ).encode("utf-8")
            return {"contentType": "application/json", "response": [body]}

    fake = FailingStepClient()
    client = AgentCoreRuntimeClient("exec-1", client=fake, pipeline_run_id=7)

    result = asyncio.run(client.run("data-selection-agent", "", {}))

    assert fake.calls == 1
    assert result["_failure_code"] == "SELECTION_RULE_INVALID"


class RetryingStepClient:
    """파생 단계가 1회 실패 후 2회차에 성공한다. 회차마다 별도 invocation이어야 한다."""

    def __init__(self):
        self.requests = []

    def invoke_agent_runtime(self, **kwargs):
        request = json.loads(kwargs["payload"])
        self.requests.append(request)
        step, attempt = request["step"], request["payload"]["attempt"]
        if step == "SOURCE_COLUMN_SELECTION":
            out = {"source_columns": [{"column": "age_band"}]}
        elif step == "DERIVED_COLUMN_DESIGN" and attempt == 1:
            out = {
                "_step_retry": {
                    "step": step,
                    "attempt": 1,
                    "error": "data_type 불일치",
                    "retry_feedback": "data_type을 맞추세요.",
                    "failure_snapshot": {"failure_code": "SELECTION_RULE_INVALID"},
                }
            }
        elif step == "DERIVED_COLUMN_DESIGN":
            out = {"derived_columns": [{"name": "d1"}]}
        else:
            out = {"sample_rows": []}
        return {
            "contentType": "application/json",
            "response": [json.dumps({"output": out}).encode("utf-8")],
        }


def test_each_retry_attempt_is_its_own_invocation(monkeypatch):
    """한 step이 3회 재시도하면 68초 한도를 넘는다. 회차도 invocation으로 나눈다."""
    _arn(monkeypatch)
    fake = RetryingStepClient()
    client = AgentCoreRuntimeClient("exec-1", client=fake, pipeline_run_id=7)

    result = asyncio.run(client.run("data-selection-agent", "", {}))

    calls = [(r["step"], r["payload"]["attempt"]) for r in fake.requests]
    assert calls == [
        ("SOURCE_COLUMN_SELECTION", 1),
        ("DERIVED_COLUMN_DESIGN", 1),
        ("DERIVED_COLUMN_DESIGN", 2),
        ("SYNTHETIC_SAMPLE_GENERATION", 1),
    ]
    # 실패 안내가 다음 회차 invocation으로 전달된다.
    assert fake.requests[2]["payload"]["retry_feedback"] == "data_type을 맞추세요."
    assert "derived_columns" in result


def test_exhausted_attempts_report_controlled_failure(monkeypatch):
    """3회를 모두 소진하면 기존과 동일한 _agent_error 계약으로 상위에 전달한다."""
    _arn(monkeypatch)

    class AlwaysRetry:
        def __init__(self):
            self.calls = 0

        def invoke_agent_runtime(self, **kwargs):
            self.calls += 1
            out = {
                "_step_retry": {
                    "step": "SOURCE_COLUMN_SELECTION",
                    "attempt": self.calls,
                    "error": "계약 위반",
                    "retry_feedback": "고치세요.",
                    "failure_snapshot": {"failure_code": "SELECTION_RULE_INVALID"},
                }
            }
            return {
                "contentType": "application/json",
                "response": [json.dumps({"output": out}).encode("utf-8")],
            }

    fake = AlwaysRetry()
    client = AgentCoreRuntimeClient("exec-1", client=fake, pipeline_run_id=7)

    result = asyncio.run(client.run("data-selection-agent", "", {}))

    assert fake.calls == 3
    assert result["_failure_code"] == "SELECTION_RULE_INVALID"
    assert "계약 위반" in result["_agent_error"]
