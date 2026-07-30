from typing import Protocol


class AgentClient(Protocol):
    async def run(self, agent_name: str, model_name: str, payload: dict) -> dict:
        ...
