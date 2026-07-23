"""같은 요청을 N번 반복 실행해서 requested_data_categories 등 필드의 일관성을 확인하는 스크립트.

실제 모델을 N번 호출하므로 시간/비용이 든다. run()의 성공 여부 자체가 아니라, 모델이 매번
같은 조건을 같은 방식으로 카테고리화하는지(비결정성 정도)를 보기 위한 용도다.

사용법(리포지토리 루트에서):
    python -m agent_runtime.requirements_analysis.check_consistency
    python -m agent_runtime.requirements_analysis.check_consistency "요청 문구" -n 10
"""

import argparse
from collections import Counter

from agent_runtime.requirements_analysis.agent import run

DEFAULT_REQUEST = "수도권 30대 고객의 여행 업종 결제 성향을 분석해서 CSV와 보고서로 제공해줘"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", nargs="?", default=DEFAULT_REQUEST, help="반복 실행할 요청 문구")
    parser.add_argument("-n", "--runs", type=int, default=5, help="반복 횟수 (기본 5)")
    args = parser.parse_args()

    results = []
    for i in range(1, args.runs + 1):
        print(f"\n=== 실행 {i}/{args.runs} ===")
        result = run(args.request)
        results.append(result)
        print(result["data"] if result["ok"] else f"실패: {result['error_message']}")

    ok_data = [r["data"] for r in results if r["ok"]]
    fail_count = len(results) - len(ok_data)

    print("\n" + "=" * 60)
    print(f"요청: {args.request}")
    print(f"성공 {len(ok_data)}/{args.runs}, 실패 {fail_count}")

    if not ok_data:
        return

    key_counter = Counter()
    for data in ok_data:
        key_counter.update(data.get("requested_data_categories", {}).keys())

    print("\n[requested_data_categories 키 등장 빈도]")
    for key, count in key_counter.most_common():
        flag = "일관됨" if count == len(ok_data) else "불일치"
        print(f"  {key}: {count}/{len(ok_data)}회 ({flag})")

    value_map: dict[str, list[str]] = {}
    for data in ok_data:
        for key, value in data.get("requested_data_categories", {}).items():
            value_map.setdefault(key, []).append(str(value))

    print("\n[카테고리별 값 일관성]")
    for key, values in value_map.items():
        distinct = set(values)
        flag = "일관됨" if len(distinct) == 1 else f"불일치 ({len(distinct)}종)"
        print(f"  {key}: {flag} -> {values}")

    for field in ("usage_purpose", "delivery_channel", "output_format"):
        values = [str(data.get(field)) for data in ok_data]
        distinct = set(values)
        flag = "일관됨" if len(distinct) == 1 else f"불일치 ({len(distinct)}종)"
        print(f"\n[{field}] {flag}")
        for v in distinct:
            print(f"  - {v} ({values.count(v)}회)")


if __name__ == "__main__":
    main()
