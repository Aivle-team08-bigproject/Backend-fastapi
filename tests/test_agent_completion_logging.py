import json

import pytest

from agent_runtime import observability
from agent_runtime.data_processing import planning_agent
from agent_runtime.data_selection import agent as data_selection_agent
from agent_runtime.requirements_analysis import agent as requirements_agent


def test_completion_tool_writes_one_file_per_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_LOG_DIR", str(tmp_path))

    log_tool = observability.build_agent_completion_tool("data-selection-agent")
    result = log_tool(
        ["메타데이터 해석", "컬럼과 합성 샘플 설계"],
        "고객 요청에 맞는 선별 계획과 합성 샘플을 만들었다.",
    )

    assert result["ok"] is True
    assert "next_action" in result
    log_path = tmp_path / "data-selection-agent.jsonl"
    record = json.loads(log_path.read_text(encoding="utf-8"))
    assert record["agent_name"] == "data-selection-agent"
    assert record["completed_tasks"] == ["메타데이터 해석", "컬럼과 합성 샘플 설계"]
    assert record["summary"]
    assert record["completed_at"]


def test_completion_tool_rejects_invalid_payload_without_writing(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_LOG_DIR", str(tmp_path))

    log_tool = observability.build_agent_completion_tool("requirement-analysis-agent")
    result = log_tool([], "")

    assert result["ok"] is False
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("module", "agent_name"),
    [
        (requirements_agent, "requirement-analysis-agent"),
        (data_selection_agent, "data-selection-agent"),
        (planning_agent, "data-processing-agent"),
    ],
)
def test_each_agent_registers_completion_tool(module, agent_name, tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setenv("AGENT_LOG_DIR", str(tmp_path))

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(module, "Agent", FakeAgent)
    monkeypatch.setattr(module, "_build_model", lambda: object())

    module.build_agent()

    assert len(captured["tools"]) == 1
    assert captured["tools"][0].tool_spec["name"] == "log_agent_completion"
    assert "log_agent_completion" in captured["system_prompt"]

    captured["tools"][0](["작업 완료"], "작업을 완료했다.")
    assert (tmp_path / f"{agent_name}.jsonl").exists()
