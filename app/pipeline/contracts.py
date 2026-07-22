"""Celery와 AgentCore가 공유할 에이전트 실행 계약.

FastAPI와 파이프라인 DB는 이 이벤트 형식만 저장·전달한다. 실행 환경별
adapter는 자체 응답을 이 모델로 변환해야 한다.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AgentEventType(str, Enum):
    PROGRESS = "progress"
    AGENT_LOG = "agent_log"
    ARTIFACT_READY = "artifact_ready"
    FAILED = "failed"


class AgentExecutionRequest(BaseModel):
    run_id: str
    stage_run_id: str
    stage_code: str
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentEvent(BaseModel):
    event_type: AgentEventType
    message: str
    progress_percent: int | None = Field(default=None, ge=0, le=100)
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentExecutionResult(BaseModel):
    succeeded: bool
    output: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
