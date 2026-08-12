"""Typed wire contract shared by the API adapter and AgentCore Runtime.

The contract deliberately keeps stage payloads opaque.  Each stage owns its
payload schema, while this module owns only the invocation envelope and the
stable execution correlation fields.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


AgentName = Literal[
    "requirement-analysis-agent",
    "data-selection-agent",
    "data-processing-agent",
]


SelectionStep = Literal[
    "SOURCE_COLUMN_SELECTION",
    "DERIVED_COLUMN_DESIGN",
    "SYNTHETIC_SAMPLE_GENERATION",
]


class AgentCoreInvocationRequest(BaseModel):
    """Request body sent to ``POST /invocations``.

    ``step`` splits a multi-prompt stage into one invocation per prompt step.
    AgentCore terminates invocations that run past roughly 68 seconds
    (2026-08-12 실측: 진짜 424 5건이 67~70초에 집중), and running all three
    data-selection prompts in a single call reliably crossed that line.  Each
    step is ~10 seconds, so splitting keeps every invocation far below it.

    Omitting ``step`` keeps the original whole-stage behaviour for callers that
    do not orchestrate steps themselves (로컬 실행, 기존 계약 호환).
    """

    model_config = ConfigDict(extra="forbid")

    agent_name: AgentName
    model_name: str = Field(default="", max_length=255)
    execution_id: str = Field(min_length=1, max_length=255)
    payload: dict[str, Any]
    step: SelectionStep | None = None


class AgentCoreInvocationResponse(BaseModel):
    """Successful Runtime response.

    The caller correlates the response with its request ``execution_id``; the
    Runtime therefore returns only the stage result and never echoes secrets
    or the full invocation envelope.
    """

    model_config = ConfigDict(extra="forbid")

    output: dict[str, Any]


class AgentCoreErrorResponse(BaseModel):
    """Stable, non-sensitive error body exposed by the Runtime."""

    model_config = ConfigDict(extra="forbid")

    error: Literal["agent_execution_failed"]
    retryable: bool
    message: str = Field(min_length=1, max_length=200)
