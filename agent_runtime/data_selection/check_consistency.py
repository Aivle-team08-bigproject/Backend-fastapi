"""요구사항 분석 -> 데이터 선별로 이어지는 실제 파이프라인을 N번 반복 실행해서
selected_tables/source_columns/derived_columns/sample_columns의 일관성을 확인하는 스크립트.

요구사항 분석은 한 번만 실행해서 고정된 analysis를 만들고, 그 analysis를 데이터 선별
에이전트에 N번 넣어서 흔들리는지 본다(requirements_analysis/check_consistency.py와 동일한
방식). 실제 모델을 호출하므로 시간/비용이 든다.

사용법(리포지토리 루트에서):
    python -m agent_runtime.data_selection.check_consistency
    python -m agent_runtime.data_selection.check_consistency "요청 문구" -n 10
"""

import argparse
import asyncio
from collections import Counter

from agent_runtime.data_selection.agent import run as run_data_selection
from agent_runtime.query.metadata import load_dataset_metadata
from agent_runtime.requirements_analysis.agent import run as run_requirements_analysis
from app.db.portfolio_agent_session import AsyncSessionLocal

DEFAULT_REQUEST = "수도권 30대 고객의 여행 업종 결제 성향을 분석해서 CSV와 보고서로 제공해줘"
AVAILABLE_DATA = ["merchant", "member_pseudonymized", "transaction_pseudonymized"]


def _to_analysis_payload(requirements_data: dict) -> dict:
    return {
        "usage_purpose": requirements_data["usage_purpose"],
        "requested_data_sentence": requirements_data["requested_data_summary"],
        "categories": requirements_data["requested_data_categories"],
        "delivery_channel": requirements_data["delivery_channel"],
        "output_formats": requirements_data["output_format"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", nargs="?", default=DEFAULT_REQUEST, help="반복 실행할 요청 문구")
    parser.add_argument("-n", "--runs", type=int, default=5, help="반복 횟수 (기본 5)")
    args = parser.parse_args()

    analysis_result = run_requirements_analysis(args.request)
    if not analysis_result["ok"]:
        print(f"요구사항 분석 실패: {analysis_result['error_message']}")
        return
    analysis_payload = _to_analysis_payload(analysis_result["data"])

    async def load_metadata():
        async with AsyncSessionLocal() as db:
            return await load_dataset_metadata(db, AVAILABLE_DATA)

    schema_metadata = asyncio.run(load_metadata())

    print("=== 요구사항 분석 결과 (데이터 선별 입력으로 고정 재사용) ===")
    print(analysis_payload)

    results = []
    for i in range(1, args.runs + 1):
        print(f"\n=== 실행 {i}/{args.runs} ===")
        result = run_data_selection(
            args.request,
            analysis_payload,
            AVAILABLE_DATA,
            schema_metadata,
        )
        results.append(result)
        print(result["data"] if result["ok"] else f"실패: {result['error_message']}")

    ok_data = [r["data"] for r in results if r["ok"]]
    fail_count = len(results) - len(ok_data)

    print("\n" + "=" * 60)
    print(f"요청: {args.request}")
    print(f"성공 {len(ok_data)}/{args.runs}, 실패 {fail_count}")

    if not ok_data:
        return

    table_counter = Counter()
    for data in ok_data:
        tables = [t.get("table") for t in data.get("selected_tables", []) if isinstance(t, dict)]
        table_counter.update(tables)

    print("\n[selected_tables 등장 빈도]")
    for table, count in table_counter.most_common():
        flag = "일관됨" if count == len(ok_data) else "불일치"
        print(f"  {table}: {count}/{len(ok_data)}회 ({flag})")

    invalid_tables = {
        t
        for data in ok_data
        for t in (x.get("table") for x in data.get("selected_tables", []) if isinstance(x, dict))
        if t not in AVAILABLE_DATA
    }
    if invalid_tables:
        print(f"\n[경고] available_data에 없는 테이블이 나옴(환각): {invalid_tables}")

    source_sets = [
        tuple(
            sorted(
                (column.get("dataset"), column.get("column"))
                for column in data.get("source_columns", [])
            )
        )
        for data in ok_data
    ]
    distinct_source_sets = set(source_sets)
    flag = "일관됨" if len(distinct_source_sets) == 1 else f"불일치 ({len(distinct_source_sets)}종)"
    print(f"\n[source_columns 구성] {flag}")
    for value in distinct_source_sets:
        print(f"  - {value} ({source_sets.count(value)}회)")

    derived_sets = [
        tuple(sorted(column.get("name") for column in data.get("derived_columns", [])))
        for data in ok_data
    ]
    distinct_derived_sets = set(derived_sets)
    flag = "일관됨" if len(distinct_derived_sets) == 1 else f"불일치 ({len(distinct_derived_sets)}종)"
    print(f"\n[derived_columns 구성] {flag}")
    for value in distinct_derived_sets:
        print(f"  - {value} ({derived_sets.count(value)}회)")

    column_sets = [
        tuple(
            sorted(
                "".join(c.get("name", "").split())
                for c in data.get("sample_columns", [])
                if isinstance(c, dict)
            )
        )
        for data in ok_data
    ]
    distinct_column_sets = set(column_sets)
    flag = "일관됨" if len(distinct_column_sets) == 1 else f"불일치 ({len(distinct_column_sets)}종)"
    print(f"\n[sample_columns 구성] {flag}")
    for v in distinct_column_sets:
        print(f"  - {v} ({column_sets.count(v)}회)")

    sample_contracts = [
        (
            len(data.get("sample_rows", [])),
            (data.get("sample_metadata") or {}).get("is_synthetic"),
            (data.get("sample_metadata") or {}).get("sample_count"),
        )
        for data in ok_data
    ]
    valid_samples = sum(contract == (5, True, 5) for contract in sample_contracts)
    print(f"\n[합성 sample_rows 계약] {valid_samples}/{len(ok_data)}회 통과")


if __name__ == "__main__":
    main()
