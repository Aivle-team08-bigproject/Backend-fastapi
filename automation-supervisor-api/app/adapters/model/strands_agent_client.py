import json

from app.adapters.model.stub_agent_client import StubAgentClient
from app.agents.strands_agent_factory import build_strands_agent, parse_agent_json_response
from app.application.ports.agent_client import AgentClient


class StrandsAgentClient(AgentClient):
    def __init__(self, fallback_to_stub: bool = True):
        self.fallback_to_stub = fallback_to_stub
        self.stub = StubAgentClient()

    async def run(self, agent_name: str, model_name: str, payload: dict) -> dict:
        agent = build_strands_agent(agent_name, model_name)
        if agent is None:
            if not self.fallback_to_stub:
                raise RuntimeError("strands-agents is not installed")
            return await self.stub.run(agent_name, model_name, payload)

        response = agent(json.dumps(payload, ensure_ascii=False))
        return parse_agent_json_response(response)
