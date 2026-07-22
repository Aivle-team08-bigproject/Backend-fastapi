from collections.abc import AsyncIterator
from typing import Protocol

from app.pipeline.contracts import AgentEvent, AgentExecutionRequest, AgentExecutionResult


class AgentRunner(Protocol):
    """실행 환경에 독립적인 에이전트 adapter 계약.

    LocalCeleryAgentRunner와 미래의 AgentCoreAgentRunner는 동일한 이벤트와 결과를
    반환한다. 호출자는 Celery task ID나 AgentCore runtime session ID를 알 필요가 없다.
    """

    async def stream(self, request: AgentExecutionRequest) -> AsyncIterator[AgentEvent]: ...

    async def result(self, request: AgentExecutionRequest) -> AgentExecutionResult: ...
