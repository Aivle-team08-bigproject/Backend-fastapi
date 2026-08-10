"""Agent runtime에서 사용할 LLM provider factory."""

from strands.models.bedrock import BedrockModel
from strands.models.openai import OpenAIModel


def build_model(
    *,
    provider: str,
    model_id: str,
    deepseek_api_key: str,
    deepseek_base_url: str,
    region_name: str,
    temperature: float = 0,
    json_mode: bool = False,
    timeout: float | None = None,
):
    """로컬은 DeepSeek, AgentCore는 AWS SDK credential chain 기반 Bedrock을 사용한다."""
    normalized_provider = provider.strip().lower()
    if normalized_provider == "bedrock":
        return BedrockModel(
            model_id=model_id,
            region_name=region_name,
            temperature=temperature,
        )
    if normalized_provider != "deepseek":
        raise ValueError(f"unsupported agent model provider: {provider}")

    client_args = {"api_key": deepseek_api_key, "base_url": deepseek_base_url}
    if timeout is not None:
        client_args["timeout"] = timeout
        client_args["max_retries"] = 0
    params = {"temperature": temperature}
    if json_mode:
        params["response_format"] = {"type": "json_object"}
    return OpenAIModel(client_args=client_args, model_id=model_id, params=params)
