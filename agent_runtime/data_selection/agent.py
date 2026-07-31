"""Neon DB COMMENT 기반 컬럼 설계 에이전트."""

import json
import re

from strands import Agent, tool
from strands.models.openai import OpenAIModel

from agent_runtime.data_selection.config import settings


SYSTEM_PROMPT = """당신은 '하나 데이터 마켓'의 DB 메타데이터 기반 컬럼 설계 에이전트다.
고객 요구사항에 필요한 기존 DB 컬럼을 선택하고, 기존 컬럼을 조합한 파생 컬럼을 설계한 뒤,
검토용 합성 더미 데이터 5건을 만든다. 실제 DB 행을 조회하거나 조회 건수를 결정하지 않는다.

사용자 메시지는 아래 형식의 JSON 문자열이다:
{
  "raw_requirement": "고객의 원본 자연어 요청",
  "analysis": {
    "usage_purpose": "...",
    "requested_data_sentence": "...",
    "categories": {"카테고리명": "값"},
    "delivery_channel": "...",
    "output_formats": ["..."]
  },
  "available_data": ["논리 데이터셋 이름"],
  "retry_feedback": "<재시도인 경우 직전 결과가 실패한 이유>",
  "schema_metadata": [
    {
      "dataset": "논리 데이터셋 이름",
      "schema": "anonymized",
      "table": "실제 테이블명",
      "comment": "DB 테이블 COMMENT",
      "columns": [
        {"name": "실제 컬럼명", "data_type": "DB 타입", "comment": "DB 컬럼 COMMENT"}
      ]
    }
  ]
}

반드시 아래 JSON 형식으로만 응답한다:
{
  "selected_tables": [
    {"table": "<available_data 중 하나>", "reason": "<DB COMMENT 근거 선택 이유>"}
  ],
  "source_columns": [
    {
      "dataset": "<selected_tables의 논리 데이터셋>",
      "column": "<schema_metadata에 실제 존재하는 컬럼명>",
      "data_type": "<schema_metadata의 DB 타입>",
      "comment": "<DB COMMENT>",
      "reason": "<고객 요청에 필요한 이유>"
    }
  ],
  "derived_columns": [
    {
      "name": "<새 파생 컬럼명>",
      "data_type": "<string|integer|number|boolean|date|datetime>",
      "source_columns": ["<source_columns에 선택된 실제 컬럼명>"],
      "derivation": "<계산 또는 조합 규칙>",
      "description": "<고객에게 보여줄 설명>"
    }
  ],
  "selection_query": {
    "columns": ["<source_columns에 선택된 실제 컬럼명>"],
    "filters": {}
  },
  "sample_columns": [
    {
      "name": "<원본 또는 파생 컬럼의 최종 표시명>",
      "data_type": "<string|integer|number|boolean|date|datetime>",
      "is_derived": <DB 원본 컬럼이면 false, 조합해 만든 컬럼이면 true>,
      "source_columns": ["<근거가 되는 실제 DB 컬럼명>"],
      "description": "<DB COMMENT 또는 파생 규칙 기반 설명>"
    }
  ],
  "sample_rows": [
    {"<sample_columns의 컬럼명>": "<실제 DB 행과 무관한 합성 값>"}
  ],
  "sample_metadata": {
    "is_synthetic": true,
    "sample_count": 5,
    "notice": "실제 고객 데이터가 아닌 형식 확인용 예시 데이터입니다."
  }
}

규칙:
- schema_metadata의 DB COMMENT를 컬럼 의미 판단의 우선 근거로 사용한다.
- selected_tables는 available_data에 있는 값만 사용한다.
- source_columns.column은 schema_metadata에 실제 존재하는 허용 컬럼만 사용한다.
- source_columns에는 고객 요청을 충족하는 데 필요한 최소 원본 컬럼만 넣는다.
- derived_columns는 반드시 source_columns만으로 계산 가능해야 한다.
- 고객 요청에 맞게 기존 컬럼을 계산·집계·분류·조합한 derived_columns를 최소 1개 만든다.
- derived_columns를 단순한 원본 컬럼의 이름 변경으로 만들지 않는다.
- retry_feedback이 있으면 실패 원인을 반드시 수정해서 전체 JSON을 다시 생성한다.
- selection_query.columns에는 source_columns의 실제 컬럼명을 중복 없이 넣는다.
- top_k, limit, vector_similarity는 절대 생성하지 않는다.
- 이 Agent는 행 필터링을 담당하지 않으므로 selection_query.filters는 항상 빈 객체로 둔다.
- sample_columns는 고객에게 최종 제공할 원본 컬럼과 파생 컬럼을 모두 설명한다.
- sample_rows는 정확히 5건이며 sample_columns의 모든 이름을 정확히 한 번씩 포함한다.
- sample_rows는 실제 DB 행을 복사하지 않은 완전한 합성 데이터여야 한다.
- 실제 고객ID, 카드번호, 전화번호, 이메일처럼 보이는 값을 만들지 않는다.
- sample_metadata는 합성 샘플 5건임을 명시한다.
- JSON 외의 텍스트, 마크다운, 서두 문구를 절대 출력하지 않는다."""


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
MAX_ATTEMPTS = 3


def _build_model() -> OpenAIModel:
    return OpenAIModel(
        client_args={
            "api_key": settings.deepseek_api_key,
            "base_url": settings.deepseek_base_url,
        },
        model_id=settings.data_selection_model_id,
        params={"temperature": 0},
    )


def build_agent() -> Agent:
    return Agent(model=_build_model(), tools=[], system_prompt=SYSTEM_PROMPT, callback_handler=None)


def _extract_json(raw_text: str) -> dict:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            first_line, rest = text.split("\n", 1)
            text = rest if first_line.strip().lower() in ("", "json") else text
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        raise ValueError("모델 응답에서 JSON을 찾지 못함")
    return json.loads(match.group(0))


def _validate_contract(data: dict, schema_metadata: list[dict]) -> None:
    selected_tables = data.get("selected_tables")
    source_columns = data.get("source_columns")
    derived_columns = data.get("derived_columns")
    query = data.get("selection_query")
    sample_columns = data.get("sample_columns")
    sample_rows = data.get("sample_rows")
    metadata = data.get("sample_metadata")

    if not isinstance(selected_tables, list) or not selected_tables:
        raise ValueError("selected_tables는 비어 있지 않은 배열이어야 함")
    if not isinstance(source_columns, list) or not source_columns:
        raise ValueError("source_columns는 비어 있지 않은 배열이어야 함")
    if not isinstance(derived_columns, list):
        raise ValueError("derived_columns는 배열이어야 함")
    if not derived_columns:
        raise ValueError("derived_columns는 최소 1개 이상이어야 함")
    if not isinstance(query, dict):
        raise ValueError("selection_query는 객체여야 함")
    if any(key in query for key in ("top_k", "limit", "vector_similarity")):
        raise ValueError("selection_query에 조회량 또는 벡터 검색 설정을 넣을 수 없음")

    allowed = {
        (dataset["dataset"], column["name"])
        for dataset in schema_metadata
        for column in dataset.get("columns", [])
    }
    selected_source_names = []
    for column in source_columns:
        key = (column.get("dataset"), column.get("column"))
        if key not in allowed:
            raise ValueError(f"DB 메타데이터에 없는 source column: {key}")
        selected_source_names.append(column["column"])
    if set(query.get("columns") or []) != set(selected_source_names):
        raise ValueError("selection_query.columns는 source_columns와 일치해야 함")
    if query.get("filters") not in ({}, None):
        raise ValueError("컬럼 설계 Agent는 행 필터를 생성할 수 없음")

    selected_source_set = set(selected_source_names)
    for column in derived_columns:
        sources = column.get("source_columns")
        if not column.get("name") or not isinstance(sources, list) or not sources:
            raise ValueError("모든 derived column에는 name과 source_columns가 필요함")
        if not set(sources).issubset(selected_source_set):
            raise ValueError("derived column은 선택된 source column만 참조해야 함")
        if not column.get("derivation"):
            raise ValueError("모든 derived column에는 derivation이 필요함")

    if not isinstance(sample_columns, list) or not sample_columns:
        raise ValueError("sample_columns는 비어 있지 않은 배열이어야 함")
    names = [column.get("name") for column in sample_columns if isinstance(column, dict)]
    if len(names) != len(sample_columns) or any(not name for name in names):
        raise ValueError("모든 sample_columns 항목에는 name이 필요함")
    if len(set(names)) != len(names):
        raise ValueError("sample_columns의 name은 중복될 수 없음")
    if not isinstance(sample_rows, list) or len(sample_rows) != 5:
        raise ValueError("sample_rows는 정확히 5건이어야 함")
    expected_keys = set(names)
    if any(not isinstance(row, dict) or set(row) != expected_keys for row in sample_rows):
        raise ValueError("sample_rows의 컬럼이 sample_columns와 일치하지 않음")
    if not isinstance(metadata, dict):
        raise ValueError("sample_metadata는 객체여야 함")
    if metadata.get("is_synthetic") is not True or metadata.get("sample_count") != 5:
        raise ValueError("sample_metadata는 합성 샘플 5건임을 표시해야 함")


@tool
def run(
    raw_requirement: str,
    analysis: dict,
    available_data: list[str],
    schema_metadata: list[dict] | None = None,
) -> dict:
    """DB 메타데이터를 근거로 원본·파생 컬럼과 합성 샘플 5건을 설계한다."""
    schema_metadata = schema_metadata or []
    request_payload = {
        "raw_requirement": raw_requirement,
        "analysis": analysis,
        "available_data": available_data,
        "schema_metadata": schema_metadata,
        "retry_feedback": None,
    }
    last_error: str | None = None
    for _attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            message = json.dumps(request_payload, ensure_ascii=False)
            result = build_agent()(message)
            parsed = _extract_json(str(result))
            _validate_contract(parsed, schema_metadata)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            request_payload["retry_feedback"] = (
                f"직전 {_attempt}회차 결과 검증 실패: {last_error}. "
                "이 문제를 수정하고 derived_columns를 포함한 전체 JSON을 다시 생성하세요."
            )
            continue
        return {"ok": True, "data": parsed, "error_message": None}
    return {
        "ok": False,
        "data": None,
        "error_message": f"{MAX_ATTEMPTS}회 시도 후에도 실패: {last_error}",
    }
