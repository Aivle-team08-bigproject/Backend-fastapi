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


class AgentCoreInvocationRequest(BaseModel):
    """Request body sent to ``POST /invocations``."""

    model_config = ConfigDict(extra="forbid")

    agent_name: AgentName
    model_name: str = Field(default="", max_length=255)
    execution_id: str = Field(min_length=1, max_length=255)
    payload: dict[str, Any]


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
