"""Neon DB COMMENT 기반 컬럼 설계 에이전트."""

import json
import re

from strands import Agent, tool
from strands.models.openai import OpenAIModel

from agent_runtime.data_selection.config import settings


FINAL_CONTRACT_REFERENCE = """당신은 '하나 데이터 마켓'의 DB 메타데이터 기반 컬럼 설계 에이전트다.
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
  "hitl_feedback": "<이전 합성 샘플에 대한 고객의 수정 의견. 없으면 null>",
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
  ],
  "reference_catalogs": [
    {
      "catalog": "카탈로그 이름",
      "comment": "카탈로그 COMMENT",
      "target_columns": [{"dataset": "논리 데이터셋", "column": "필터 대상 컬럼"}],
      "entries": [{"mcc_code": 4722, "mcc_name": "여행사"}]
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
    "filters": {
      "<필터에 사용할 실제 컬럼명>": {
        "operator": "<eq|in|gte|lte|between|starts_with>",
        "value": "<단일 값 또는 in/between용 배열>",
        "reason": "<사용자 요구사항에서 이 조건이 필요한 이유>",
        "evidence": "<해당 테이블·컬럼 COMMENT를 근거로 한 해석>"
      }
    }
  },
  "interpretations": [
    {
      "term": "<사용자 요청에서 의미 해석이 필요한 표현>",
      "interpreted_as": "<COMMENT를 종합해 선택한 가장 가까운 의미>",
      "reason": "<그 의미를 선택한 근거>",
      "requires_confirmation": true
    }
  ],
  "catalog_issues": [
    {
      "term": "<실행 조건으로 확정할 근거가 부족한 표현>",
      "reason": "<현재 COMMENT만으로 확정할 수 없는 이유>",
      "required_information": "<추가로 필요한 COMMENT 또는 카탈로그 정보>"
    }
  ],
  "catalog_matches": [
    {
      "term": "<사용자 요청의 카테고리 표현>",
      "catalog": "<사용한 reference_catalogs의 이름>",
      "matches": [
        {"code": "<카탈로그의 실제 코드>", "label": "<카탈로그의 실제 이름>"}
      ],
      "reason": "<이 항목들을 가장 가까운 의미로 선택한 이유>",
      "requires_confirmation": true
    }
  ],
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
- 자연어 업종처럼 실제 코드값 변환이 필요한 조건은 reference_catalogs의 COMMENT와 entries를
  함께 사용하여 의미적으로 가장 가까운 항목들을 선택한다.
- reference_catalogs에 실행 가능한 후보가 있으면 해당 실제 코드를 filters에 넣고,
  선택한 코드·이름·근거를 catalog_matches에 공개한다. 이 경우 같은 조건을
  catalog_issues로 보내지 않는다.
- reference_catalogs에 없는 코드나 이름을 기억 또는 상식으로 만들어내지 않는다.
- COMMENT와 고객 요구사항을 종합하여 가장 적합하고 가까운 의미를 우선 선택한다.
- 의미가 여러 가지일 수 있다는 이유만으로 작업을 중단하거나 catalog_issues로 보내지 않는다.
  실행 가능한 최선의 해석으로 필터와 합성 샘플을 만들고 interpretations에 그 근거를 공개한다.
- 고객 의도와 다를 가능성이 있는 합리적 해석은 requires_confirmation을 true로 표시해 HITL에서
  확인할 수 있게 한다.
- catalog_issues는 COMMENT를 종합해도 유효한 실행 값이나 관계를 만들 수 없을 때만 사용한다.
- hitl_feedback이 있으면 이전 해석보다 우선하여 고객의 수정 의도를 새 필터와 샘플에 반영한다.
- selected_tables는 available_data에 있는 값만 사용한다.
- source_columns.column은 schema_metadata에 실제 존재하는 허용 컬럼만 사용한다.
- source_columns에는 고객 요청을 충족하는 데 필요한 최소 원본 컬럼만 넣는다.
- derived_columns는 반드시 source_columns만으로 계산 가능해야 한다.
- 고객 요청에 맞게 기존 컬럼을 계산·집계·분류·조합한 derived_columns를 최소 1개 만든다.
- derived_columns를 단순한 원본 컬럼의 이름 변경으로 만들지 않는다.
- retry_feedback이 있으면 실패 원인을 반드시 수정해서 전체 JSON을 다시 생성한다.
- selection_query.columns에는 source_columns의 실제 컬럼명을 중복 없이 넣는다.
- top_k, limit, vector_similarity는 절대 생성하지 않는다.
- 사용자 요구사항의 구체적인 대상·범위·조건은 DB COMMENT를 근거로 selection_query.filters에 변환한다.
- 필터 키는 source_columns에 선택한 실제 DB 컬럼명만 사용한다.
- 필터 operator는 eq, in, gte, lte, between, starts_with 중 하나만 사용한다.
- 문자열 접두 범위(예: '서울특별시'로 시작하는 지역)는 starts_with를 사용한다.
  문자열 범위를 gte, lte, between으로 표현하지 않는다.
- 필터의 reason에는 사용자 요구사항과의 관계를, evidence에는 DB COMMENT 기반 판단 근거를 적는다.
- COMMENT를 종합해 가장 가까운 의미를 합리적으로 선택할 수 있으면 그 값으로 필터를 만들고,
  해석 내용을 interpretations에 공개한다.
- 여러 의미가 가능하거나 고객 의도 확인이 필요한 해석은 requires_confirmation을 true로 표시한다.
- COMMENT만으로 실제 실행 값을 정할 근거가 없으면 값을 추측하거나 하드코딩하지 않는다.
  해당 조건은 필터에서 제외하고 catalog_issues에 보완 사유와 필요한 정보를 기록한다.
- 필터링 조건이 없는 요청이면 selection_query.filters는 빈 객체일 수 있다.
- interpretations와 catalog_issues는 해당 항목이 없으면 빈 배열로 둔다.
- catalog_matches는 사용한 카탈로그가 없으면 빈 배열로 둔다.
- sample_columns는 고객에게 최종 제공할 원본 컬럼과 파생 컬럼을 모두 설명한다.
- sample_rows는 정확히 5건이며 sample_columns의 모든 이름을 정확히 한 번씩 포함한다.
- sample_rows는 실제 DB 행을 복사하지 않은 완전한 합성 데이터여야 한다.
- 실제 고객ID, 카드번호, 전화번호, 이메일처럼 보이는 값을 만들지 않는다.
- sample_metadata는 합성 샘플 5건임을 명시한다.
- JSON 외의 텍스트, 마크다운, 서두 문구를 절대 출력하지 않는다."""


SOURCE_COLUMN_SELECTION_PROMPT = """당신은 하나 데이터 마켓 데이터 선별 Agent의
원본 컬럼 선별 단계다. 고객 요구사항, 요구사항 분석, DB COMMENT 메타데이터와 기준
카탈로그만 사용해 필요한 테이블·원본 컬럼·필터를 결정한다. 파생 컬럼과 합성 샘플은
만들지 않는다. hitl_feedback이 있으면 이전 해석보다 우선하고 retry_feedback이 있으면
직전 검증 실패를 수정한다.

반드시 다음 JSON 하나만 반환한다:
{
  "selected_tables": [{"table": "available_data의 논리명", "reason": "COMMENT 근거"}],
  "source_columns": [{
    "dataset": "선택 데이터셋", "column": "실제 컬럼명", "data_type": "DB 타입",
    "comment": "DB COMMENT", "reason": "요구에 필요한 이유"
  }],
  "selection_query": {
    "columns": ["선택한 실제 컬럼명"],
    "filters": {"컬럼명": {
      "operator": "eq|in|gte|lte|between|starts_with", "value": "값 또는 배열",
      "reason": "사용자 요구 근거", "evidence": "COMMENT·카탈로그 근거"
    }}
  },
  "interpretations": [{
    "term": "해석 대상", "interpreted_as": "선택한 의미", "reason": "근거",
    "requires_confirmation": true
  }],
  "catalog_issues": [{
    "term": "근거 부족 표현", "reason": "확정 불가 사유",
    "required_information": "추가 COMMENT·카탈로그"
  }],
  "catalog_matches": [{
    "term": "카테고리 표현", "catalog": "제공된 카탈로그 이름",
    "matches": [{"code": "실제 코드", "label": "실제 이름"}],
    "reason": "선택 근거", "requires_confirmation": true
  }]
}

규칙:
- selected_tables는 available_data, source_columns는 schema_metadata에 실제 존재하는 값만 쓴다.
- DB COMMENT를 의미 판단의 우선 근거로 사용하고 필요한 최소 컬럼만 선택한다.
- 자연어 카테고리는 제공된 reference_catalogs의 실제 항목만 사용한다.
- 가장 가까운 실행 가능한 의미를 선택하되 interpretations에 근거와 확인 필요 여부를 공개한다.
- 실행 근거가 없을 때만 필터에서 제외하고 catalog_issues에 기록한다.
- selection_query.columns는 source_columns와 정확히 일치시킨다.
- top_k, limit, vector_similarity는 만들지 않는다.
- 필터는 선택한 컬럼만 사용하고 reason과 evidence를 반드시 포함한다.
- 문자열 접두 범위에는 starts_with를 사용하고 문자열에 수치 범위 연산을 쓰지 않는다.
- JSON 외 텍스트를 출력하지 않는다."""


DERIVED_COLUMN_DESIGN_PROMPT = """당신은 하나 데이터 마켓 데이터 선별 Agent의
파생 컬럼 정의 단계다. 입력의 source_selection은 앞 단계에서 이미 검증된 결과다.
그 범위를 변경하거나 새 원본 컬럼을 추가하지 말고 고객 목적에 필요한 파생 컬럼만 설계한다.
hitl_feedback이 있으면 승인된 원본 범위 안에서 우선 반영하고 retry_feedback의 실패를 수정한다.

반드시 다음 JSON 하나만 반환한다:
{
  "derived_columns": [{
    "name": "새 파생 컬럼명",
    "data_type": "string|integer|number|boolean|date|datetime",
    "source_columns": ["검증된 원본 컬럼명"],
    "derivation": "계산·집계·분류·조합 규칙",
    "description": "고객에게 보여줄 설명"
  }]
}

규칙:
- derived_columns는 최소 1개 만든다.
- source_selection.source_columns에 있는 실제 컬럼만 참조한다.
- 단순 이름 변경은 파생 컬럼으로 만들지 않는다.
- 이름은 중복될 수 없고 원본 컬럼명과도 충돌하지 않게 한다.
- JSON 외 텍스트를 출력하지 않는다."""


SYNTHETIC_SAMPLE_GENERATION_PROMPT = """당신은 하나 데이터 마켓 데이터 선별 Agent의
합성 샘플 생성 단계다. 입력의 source_selection과 derived_design은 앞 단계에서 검증된
컬럼 설계다. 설계를 변경하지 말고 고객이 형식을 검토할 수 있는 완전한 합성 데이터 5건을 만든다.
hitl_feedback이 있으면 승인된 컬럼 설계 안에서 우선 반영하고 retry_feedback의 실패를 수정한다.

반드시 다음 JSON 하나만 반환한다:
{
  "sample_columns": [{
    "name": "최종 표시명",
    "data_type": "string|integer|number|boolean|date|datetime",
    "is_derived": true,
    "source_columns": ["근거 원본 컬럼명"],
    "description": "COMMENT 또는 파생 규칙 기반 설명"
  }],
  "sample_rows": [{"sample_columns의 이름": "합성 값"}],
  "sample_metadata": {
    "is_synthetic": true,
    "sample_count": 5,
    "notice": "실제 고객 데이터가 아닌 형식 확인용 예시 데이터입니다."
  }
}

규칙:
- sample_columns는 검증된 원본·파생 컬럼 설계로만 구성한다.
- sample_rows는 정확히 5건이고 모든 행의 키는 sample_columns 이름과 정확히 일치한다.
- 실제 DB 행을 조회·복사하지 않는다.
- 실제 고객ID, 카드번호, 전화번호, 이메일처럼 보이는 값을 만들지 않는다.
- JSON 외 텍스트를 출력하지 않는다."""


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


def build_agent(system_prompt: str) -> Agent:
    return Agent(model=_build_model(), tools=[], system_prompt=system_prompt, callback_handler=None)


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


def _validate_contract(
    data: dict,
    schema_metadata: list[dict],
    reference_catalogs: list[dict] | None = None,
) -> None:
    reference_catalogs = reference_catalogs or []
    selected_tables = data.get("selected_tables")
    source_columns = data.get("source_columns")
    derived_columns = data.get("derived_columns")
    query = data.get("selection_query")
    interpretations = data.get("interpretations")
    catalog_issues = data.get("catalog_issues")
    catalog_matches = data.get("catalog_matches")
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
    metadata_by_column = {
        (dataset["dataset"], column["name"]): column
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
    filters = query.get("filters")
    if not isinstance(filters, dict):
        raise ValueError("selection_query.filters는 객체여야 함")
    selected_source_set = set(selected_source_names)
    for column_name, condition in filters.items():
        if column_name not in selected_source_set:
            raise ValueError("필터는 선택된 source column만 사용할 수 있음")
        if not isinstance(condition, dict):
            raise ValueError("각 필터에는 operator, value, reason, evidence가 필요함")
        operator = condition.get("operator")
        value = condition.get("value")
        if operator not in {"eq", "in", "gte", "lte", "between", "starts_with"}:
            raise ValueError(f"지원하지 않는 필터 연산자: {operator}")
        if value is None or value == "" or value == []:
            raise ValueError("필터 value는 비어 있을 수 없음")
        if operator in {"in", "between"} and not isinstance(value, list):
            raise ValueError(f"{operator} 필터 value는 배열이어야 함")
        if operator == "between" and len(value) != 2:
            raise ValueError("between 필터 value는 정확히 2개여야 함")
        if operator == "starts_with" and not isinstance(value, str):
            raise ValueError("starts_with 필터 value는 문자열이어야 함")
        source = next(
            column for column in source_columns if column.get("column") == column_name
        )
        column_metadata = metadata_by_column[(source.get("dataset"), column_name)]
        data_type = str(column_metadata.get("data_type", "")).lower()
        if operator in {"gte", "lte", "between"} and any(
            text_type in data_type for text_type in ("char", "text")
        ):
            raise ValueError("문자열 범위에는 gte, lte, between을 사용할 수 없음")
        if not condition.get("reason") or not condition.get("evidence"):
            raise ValueError("각 필터에는 reason과 COMMENT 기반 evidence가 필요함")

    if not isinstance(interpretations, list):
        raise ValueError("interpretations는 배열이어야 함")
    for item in interpretations:
        if (
            not isinstance(item, dict)
            or not item.get("term")
            or not item.get("interpreted_as")
            or not item.get("reason")
            or not isinstance(item.get("requires_confirmation"), bool)
        ):
            raise ValueError("모든 interpretation에는 해석 내용과 확인 필요 여부가 필요함")

    if not isinstance(catalog_issues, list):
        raise ValueError("catalog_issues는 배열이어야 함")
    for item in catalog_issues:
        if (
            not isinstance(item, dict)
            or not item.get("term")
            or not item.get("reason")
            or not item.get("required_information")
        ):
            raise ValueError("모든 catalog issue에는 사유와 필요한 정보가 필요함")

    catalogs_by_name = {
        catalog.get("catalog"): catalog
        for catalog in reference_catalogs
        if isinstance(catalog, dict) and catalog.get("catalog")
    }
    if not isinstance(catalog_matches, list):
        raise ValueError("catalog_matches는 배열이어야 함")
    for item in catalog_matches:
        if (
            not isinstance(item, dict)
            or not item.get("term")
            or not item.get("catalog")
            or not isinstance(item.get("matches"), list)
            or not item.get("matches")
            or not item.get("reason")
            or not isinstance(item.get("requires_confirmation"), bool)
        ):
            raise ValueError("모든 catalog match에는 실제 매칭과 선택 근거가 필요함")
        catalog = catalogs_by_name.get(item["catalog"])
        if catalog is None:
            raise ValueError(f"제공되지 않은 reference catalog: {item['catalog']}")
        entries = {
            (entry.get("mcc_code"), entry.get("mcc_name"))
            for entry in catalog.get("entries", [])
            if isinstance(entry, dict)
        }
        for match in item["matches"]:
            if (
                not isinstance(match, dict)
                or (match.get("code"), match.get("label")) not in entries
            ):
                raise ValueError("catalog match는 실제 reference catalog 항목이어야 함")

    mcc_catalog = catalogs_by_name.get("mcc_codes")
    if mcc_catalog and "mcc_code" in filters:
        condition = filters["mcc_code"]
        raw_values = condition.get("value")
        values = raw_values if isinstance(raw_values, list) else [raw_values]
        allowed_codes = {
            entry.get("mcc_code")
            for entry in mcc_catalog.get("entries", [])
            if isinstance(entry, dict)
        }
        if not set(values).issubset(allowed_codes):
            raise ValueError("mcc_code 필터는 실제 MCC 카탈로그 코드만 사용할 수 있음")

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


SOURCE_RESULT_KEYS = {
    "selected_tables",
    "source_columns",
    "selection_query",
    "interpretations",
    "catalog_issues",
    "catalog_matches",
}
DERIVED_RESULT_KEYS = {"derived_columns"}
SAMPLE_RESULT_KEYS = {"sample_columns", "sample_rows", "sample_metadata"}


def _assert_exact_keys(data: dict, expected: set[str], step_name: str) -> None:
    if not isinstance(data, dict):
        raise ValueError(f"{step_name} 결과는 JSON 객체여야 함")
    missing = expected - set(data)
    extra = set(data) - expected
    if missing:
        raise ValueError(f"{step_name} 결과 필수 키 누락: {', '.join(sorted(missing))}")
    if extra:
        raise ValueError(f"{step_name} 결과에 허용되지 않은 키: {', '.join(sorted(extra))}")


def _placeholder_sample(source_column: str) -> dict:
    column_name = "__contract_placeholder__"
    return {
        "sample_columns": [
            {
                "name": column_name,
                "data_type": "string",
                "is_derived": True,
                "source_columns": [source_column],
                "description": "단계 계약 검증용 임시 샘플",
            }
        ],
        "sample_rows": [{column_name: f"synthetic-{index}"} for index in range(1, 6)],
        "sample_metadata": {"is_synthetic": True, "sample_count": 5},
    }


def _validate_source_contract(
    data: dict,
    schema_metadata: list[dict],
    reference_catalogs: list[dict],
) -> None:
    _assert_exact_keys(data, SOURCE_RESULT_KEYS, "원본 컬럼 선별")
    source_columns = data.get("source_columns")
    if not isinstance(source_columns, list) or not source_columns:
        raise ValueError("source_columns는 비어 있지 않은 배열이어야 함")
    first_source = source_columns[0].get("column") if isinstance(source_columns[0], dict) else None
    if not first_source:
        raise ValueError("모든 source column에는 실제 컬럼명이 필요함")
    validation_payload = {
        **data,
        "derived_columns": [
            {
                "name": "__contract_derived__",
                "data_type": "string",
                "source_columns": [first_source],
                "derivation": "단계 계약 검증용 조합",
                "description": "단계 계약 검증용 파생 컬럼",
            }
        ],
        **_placeholder_sample(first_source),
    }
    _validate_contract(validation_payload, schema_metadata, reference_catalogs)


def _validate_derived_contract(
    data: dict,
    source_result: dict,
    schema_metadata: list[dict],
    reference_catalogs: list[dict],
) -> None:
    _assert_exact_keys(data, DERIVED_RESULT_KEYS, "파생 컬럼 정의")
    source_columns = source_result.get("source_columns") or []
    first_source = source_columns[0].get("column") if source_columns else None
    if not first_source:
        raise ValueError("검증된 source_columns가 없어 파생 컬럼을 검증할 수 없음")
    derived_columns = data.get("derived_columns")
    if not isinstance(derived_columns, list) or not derived_columns:
        raise ValueError("derived_columns는 최소 1개 이상이어야 함")
    names = [item.get("name") for item in derived_columns if isinstance(item, dict)]
    if len(names) != len(derived_columns) or any(not name for name in names):
        raise ValueError("모든 derived column에는 name이 필요함")
    if len(names) != len(set(names)):
        raise ValueError("derived column 이름은 중복될 수 없음")
    source_names = {item.get("column") for item in source_columns if isinstance(item, dict)}
    if set(names) & source_names:
        raise ValueError("derived column 이름은 원본 컬럼명과 충돌할 수 없음")
    allowed_types = {"string", "integer", "number", "boolean", "date", "datetime"}
    if any(
        item.get("data_type") not in allowed_types or not item.get("description")
        for item in derived_columns
    ):
        raise ValueError("모든 derived column에는 허용 data_type과 description이 필요함")
    merged = {**source_result, **data, **_placeholder_sample(first_source)}
    _validate_contract(merged, schema_metadata, reference_catalogs)


def _validate_sample_contract(
    data: dict,
    source_result: dict,
    derived_result: dict,
    schema_metadata: list[dict],
    reference_catalogs: list[dict],
) -> None:
    _assert_exact_keys(data, SAMPLE_RESULT_KEYS, "합성 샘플 생성")
    _validate_contract(
        {**source_result, **derived_result, **data},
        schema_metadata,
        reference_catalogs,
    )


def _run_prompt_step(
    *,
    system_prompt: str,
    request_payload: dict,
    validate,
    step_label: str,
    step_code: str,
    on_step=None,
) -> dict:
    if on_step is not None:
        on_step(step_code, "RUNNING", None)
    last_error: str | None = None
    retry_payload = {**request_payload, "retry_feedback": None}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = build_agent(system_prompt)(
                json.dumps(retry_payload, ensure_ascii=False)
            )
            parsed = _extract_json(str(raw))
            validate(parsed)
        except Exception as exc:  # noqa: BLE001 - 모델 계약 실패를 단계 안에서 재시도
            last_error = str(exc)
            retry_payload["retry_feedback"] = (
                f"직전 {attempt}회차 {step_label} 결과 검증 실패: {last_error}. "
                "다른 단계 결과를 만들지 말고 현재 단계 JSON만 수정해 다시 생성하세요."
            )
        else:
            # 상태 저장 실패를 LLM 출력 실패로 오인해 모델을 재호출하지 않는다.
            if on_step is not None:
                on_step(step_code, "COMPLETED", _step_summary(step_code, parsed))
            return parsed
    error_message = f"{step_label} 단계가 {MAX_ATTEMPTS}회 시도 후에도 실패: {last_error}"
    if on_step is not None:
        on_step(
            step_code,
            "FAILED",
            {"validation_errors": [last_error] if last_error else []},
        )
    raise ValueError(error_message)


def _step_summary(step_code: str, result: dict) -> dict:
    """중간 산출물 대신 화면·운영에 필요한 작은 요약 지표만 반환한다."""
    if step_code == "SOURCE_COLUMN_SELECTION":
        return {
            "selected_table_count": len(result.get("selected_tables") or []),
            "source_column_count": len(result.get("source_columns") or []),
        }
    if step_code == "DERIVED_COLUMN_DESIGN":
        return {"derived_column_count": len(result.get("derived_columns") or [])}
    return {"sample_count": len(result.get("sample_rows") or [])}


def run_steps(
    raw_requirement: str,
    analysis: dict,
    available_data: list[str],
    schema_metadata: list[dict] | None = None,
    hitl_feedback: str | None = None,
    reference_catalogs: list[dict] | None = None,
    on_step=None,
) -> dict:
    """세 프롬프트를 실행하며 선택적으로 단계 상태 콜백을 호출한다."""
    schema_metadata = schema_metadata or []
    reference_catalogs = reference_catalogs or []
    common_payload = {
        "raw_requirement": raw_requirement,
        "analysis": analysis,
        "available_data": available_data,
        "schema_metadata": schema_metadata,
        "reference_catalogs": reference_catalogs,
        "hitl_feedback": hitl_feedback,
    }
    source_result = _run_prompt_step(
        system_prompt=SOURCE_COLUMN_SELECTION_PROMPT,
        request_payload=common_payload,
        validate=lambda result: _validate_source_contract(
            result, schema_metadata, reference_catalogs
        ),
        step_label="원본 컬럼 선별",
        step_code="SOURCE_COLUMN_SELECTION",
        on_step=on_step,
    )
    derived_result = _run_prompt_step(
        system_prompt=DERIVED_COLUMN_DESIGN_PROMPT,
        request_payload={**common_payload, "source_selection": source_result},
        validate=lambda result: _validate_derived_contract(
            result, source_result, schema_metadata, reference_catalogs
        ),
        step_label="파생 컬럼 정의",
        step_code="DERIVED_COLUMN_DESIGN",
        on_step=on_step,
    )
    sample_result = _run_prompt_step(
        system_prompt=SYNTHETIC_SAMPLE_GENERATION_PROMPT,
        request_payload={
            **common_payload,
            "source_selection": source_result,
            "derived_design": derived_result,
        },
        validate=lambda result: _validate_sample_contract(
            result, source_result, derived_result, schema_metadata, reference_catalogs
        ),
        step_label="합성 샘플 생성",
        step_code="SYNTHETIC_SAMPLE_GENERATION",
        on_step=on_step,
    )
    final_result = {**source_result, **derived_result, **sample_result}
    _validate_contract(final_result, schema_metadata, reference_catalogs)
    return final_result


@tool
def run(
    raw_requirement: str,
    analysis: dict,
    available_data: list[str],
    schema_metadata: list[dict] | None = None,
    hitl_feedback: str | None = None,
    reference_catalogs: list[dict] | None = None,
) -> dict:
    """원본 선별→파생 정의→합성 샘플을 독립 프롬프트로 순차 실행한다."""
    try:
        final_result = run_steps(
            raw_requirement,
            analysis,
            available_data,
            schema_metadata,
            hitl_feedback,
            reference_catalogs,
        )
        return {"ok": True, "data": final_result, "error_message": None}
    except Exception as exc:  # noqa: BLE001 - tool 응답 계약은 실패를 JSON으로 반환
        return {"ok": False, "data": None, "error_message": str(exc)}
