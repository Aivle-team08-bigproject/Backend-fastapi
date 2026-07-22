"""요구사항 분석 -> 데이터 선별로 이어지는 실제 파이프라인을 N번 반복 실행해서
selected_tables/selection_query/sample_columns의 일관성을 확인하는 스크립트.

요구사항 분석은 한 번만 실행해서 고정된 analysis를 만들고, 그 analysis를 데이터 선별
에이전트에 N번 넣어서 흔들리는지 본다(requirements_analysis/check_consistency.py와 동일한
방식). 실제 모델을 호출하므로 시간/비용이 든다.

사용법(리포지토리 루트에서):
    python -m agent_runtime.data_selection.check_consistency
    python -m agent_runtime.data_selection.check_consistency "요청 문구" -n 10
"""

import argparse
from collections import Counter

from agent_runtime.data_selection.agent import run as run_data_selection
from agent_runtime.requirements_analysis.agent import run as run_requirements_analysis

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

    print("=== 요구사항 분석 결과 (데이터 선별 입력으로 고정 재사용) ===")
    print(analysis_payload)

    results = []
    for i in range(1, args.runs + 1):
        print(f"\n=== 실행 {i}/{args.runs} ===")
        result = run_data_selection(args.request, analysis_payload, AVAILABLE_DATA)
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

    top_k_values = [data.get("selection_query", {}).get("top_k") for data in ok_data]
    distinct_top_k = set(top_k_values)
    flag = "일관됨" if len(distinct_top_k) == 1 else f"불일치 ({len(distinct_top_k)}종)"
    print(f"\n[selection_query.top_k] {flag} -> {top_k_values}")

    filters_values = [str(data.get("selection_query", {}).get("filters")) for data in ok_data]
    distinct_filters = set(filters_values)
    flag = "일관됨" if len(distinct_filters) == 1 else f"불일치 ({len(distinct_filters)}종)"
    print(f"\n[selection_query.filters] {flag}")
    for v in distinct_filters:
        print(f"  - {v} ({filters_values.count(v)}회)")

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


if __name__ == "__main__":
    main()
