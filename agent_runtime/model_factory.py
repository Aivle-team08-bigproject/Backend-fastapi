"""Agent runtime에서 사용할 LLM provider factory."""

import os

from botocore.config import Config as BotocoreConfig
from strands.models.bedrock import BedrockModel
from strands.models.openai import OpenAIModel

# strands BedrockModel의 기본 read timeout은 120초로, AgentCore가 invocation을 끊는
# 68초(2026-08-12 실측)보다 길다. 그래서 Bedrock 응답이 지연되면 우리 코드가 오류를
# 내기 전에 invocation이 먼저 죽고, 호출자는 원인을 알 수 없는 424만 받는다.
# 한도 안에서 먼저 끊어 정상적인 재시도 경로로 보낸다.
#
# invocation 예산: 메타데이터 조회 ~2s + LLM read timeout + 검증 + 상태 flush ~2s.
_BEDROCK_READ_TIMEOUT_SECONDS = int(os.getenv("AGENT_BEDROCK_READ_TIMEOUT_SECONDS", "40"))
_BEDROCK_CONNECT_TIMEOUT_SECONDS = int(os.getenv("AGENT_BEDROCK_CONNECT_TIMEOUT_SECONDS", "10"))


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
            # SDK 내부 재시도는 끈다. 재시도는 회차 단위로 별도 invocation에서 수행하므로
            # 여기서 또 재시도하면 한 invocation이 read_timeout의 배수만큼 길어진다.
            boto_client_config=BotocoreConfig(
                connect_timeout=_BEDROCK_CONNECT_TIMEOUT_SECONDS,
                read_timeout=_BEDROCK_READ_TIMEOUT_SECONDS,
                retries={"mode": "standard", "total_max_attempts": 1},
            ),
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
