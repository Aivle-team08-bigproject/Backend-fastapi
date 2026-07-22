import json
from typing import Any

try:
    from strands import Agent
except Exception:  # pragma: no cover - allows local structure tests before installing strands.
    Agent = None

from app.agents.prompts import (
    DATA_PROCESSING_SYSTEM_PROMPT,
    DATA_SELECTION_SYSTEM_PROMPT,
    HITL_FEEDBACK_SYSTEM_PROMPT,
    REQUIREMENT_ANALYSIS_SYSTEM_PROMPT,
)


PROMPTS = {
    "requirement-analysis-agent": REQUIREMENT_ANALYSIS_SYSTEM_PROMPT,
    "data-selection-agent": DATA_SELECTION_SYSTEM_PROMPT,
    "data-processing-agent": DATA_PROCESSING_SYSTEM_PROMPT,
    "hitl-feedback-classifier-agent": HITL_FEEDBACK_SYSTEM_PROMPT,
}


def build_strands_agent(agent_name: str, model_name: str) -> Any:
    if Agent is None:
        return None
    return Agent(
        model=model_name,
        system_prompt=PROMPTS[agent_name],
    )


def parse_agent_json_response(response: Any) -> dict:
    if isinstance(response, dict):
        return response
    text = str(response)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise
