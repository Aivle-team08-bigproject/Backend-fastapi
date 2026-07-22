import argparse
import asyncio
import csv
import json
import sys
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.adapters.model.stub_agent_client import StubAgentClient
from app.adapters.model.strands_agent_client import StrandsAgentClient
from app.application.validation import validate_stage_output
from app.domain.enums import StageName
from app.policies.rollback_policy import classify_hitl_feedback, rollback_stage_for


SAMPLES = {
    "travel": "수도권과 비수도권의 여행 취미 회원 결제 성향을 분석해서 API, CSV, 시각화, 보고서로 제공해줘.",
    "cafe": "20대 여성 고객의 카페와 디저트 업종 이용 패턴을 시간대별로 분석해서 CSV와 시각화 자료로 제공해줘.",
    "parenting": "육아 가구의 키즈카페와 교육 업종 결제 데이터를 지역별로 비교해서 보고서로 정리해줘.",
    "fitness": "헬스와 피트니스 업종을 자주 이용하는 회원군의 월별 결제 변화를 API와 차트로 제공해줘.",
    "subscription": "OTT와 디지털 콘텐츠 정기결제 이용자의 구독 유지 경향을 분석해서 보고서와 CSV로 만들어줘.",
}


async def main() -> None:
    args = parse_args()
    raw_requirement = args.requirement or SAMPLES[args.sample]
    # --client real: requirement-analysis-agent/data-selection-agent는 agent_runtime의 실제
    # 에이전트를 호출한다(StrandsAgentClient의 전용 분기). data-processing-agent는 아직 실제
    # 구현이 없어서(자리표시자 모델명) 실패할 수 있는데, 이는 예상된 상태라 아래에서 따로 감싼다.
    client = StrandsAgentClient() if args.client == "real" else StubAgentClient()

    print("RAW_REQUIREMENT")
    print(raw_requirement)

    analysis = await client.run(
        "requirement-analysis-agent",
        "sonnet-4.6",
        {"raw_requirement": raw_requirement},
    )
    print_result("REQUIREMENT_ANALYSIS", analysis)

    selection = await client.run(
        "data-selection-agent",
        "aws-nova",
        {
            "raw_requirement": raw_requirement,
            "analysis": analysis,
            "available_data": ["merchant", "member_pseudonymized", "transaction_pseudonymized"],
        },
    )
    print_result("DATA_SELECTION", selection)

    selected_data_summary = summarize_csv(args.csv) if args.csv else {}
    if selected_data_summary:
        print("SELECTED_DATA_SUMMARY")
        print(json.dumps(selected_data_summary, ensure_ascii=False))

    try:
        processing = await client.run(
            "data-processing-agent",
            "chatgpt-5.5",
            {
                "raw_requirement": raw_requirement,
                "analysis": analysis,
                "selection": selection,
                "selected_data_summary": selected_data_summary,
            },
        )
        print_result("DATA_PROCESSING", processing)
    except Exception as exc:  # noqa: BLE001 - data-processing-agent는 아직 실제 구현이 없어
        # --client real일 때 예상되는 실패다(자리표시자 모델명 "chatgpt-5.5"라 실제 모델 연결이
        # 안 됨). 여기서 멈추지 않고 이전 단계(REQUIREMENT_ANALYSIS/DATA_SELECTION) 결과는
        # 그대로 확인할 수 있게 한다.
        print("DATA_PROCESSING")
        print(f"(건너뜀 - 아직 실제 구현 없음, 예상된 실패): {exc}")

    approved_review = {
        "approved": True,
        "reviewer": "human-reviewer",
        "natural_feedback": "최종 산출물이 요구사항과 일치합니다. 승인합니다.",
        "failure_code": None,
    }
    print_result("HITL_REVIEW", approved_review)

    failure_code = classify_hitl_feedback(args.feedback)
    rollback_stage = rollback_stage_for(failure_code.value)
    rejected_review = {
        "approved": False,
        "reviewer": "human-reviewer",
        "natural_feedback": args.feedback,
        "failure_code": failure_code.value,
    }
    print_result("HITL_REVIEW", rejected_review)
    print("ROLLBACK_TEST")
    print(json.dumps({"failure_code": failure_code.value, "rollback_to": rollback_stage.value}, ensure_ascii=False))


def print_result(stage_name: str, artifact: dict) -> None:
    validation = validate_stage_output(StageName(stage_name), artifact)
    print(stage_name)
    print(json.dumps({"validation": validation, "artifact": artifact}, ensure_ascii=False))


def summarize_csv(csv_path: str) -> dict:
    path = Path(csv_path)
    encodings = ["utf-8-sig", "utf-8", "cp949"]
    last_error: Exception | None = None

    for encoding in encodings:
        try:
            with path.open("r", encoding=encoding, newline="") as file:
                rows = list(csv.DictReader(file))
            break
        except UnicodeDecodeError as exc:
            last_error = exc
    else:
        raise RuntimeError(f"CSV encoding failed: {last_error}")

    row_count = len(rows)
    columns = list(rows[0].keys()) if rows else []
    spend_counter = Counter(row.get("spend_category", "") for row in rows if row.get("spend_category"))
    country_counter = Counter(row.get("destination_country_name", "") for row in rows if row.get("destination_country_name"))
    gender_counter = Counter(row.get("gender", "") for row in rows if row.get("gender"))
    total_amount = sum(to_float(row.get("krw_converted_amount")) for row in rows)
    avg_amount = round(total_amount / row_count, 2) if row_count else 0

    return {
        "source_file": str(path),
        "row_count": row_count,
        "columns": columns,
        "top_spend_categories": spend_counter.most_common(5),
        "top_destination_countries": country_counter.most_common(5),
        "gender_distribution": dict(gender_counter),
        "total_amount": round(total_amount, 2),
        "avg_amount": avg_amount,
        "primary_dimension": "spend_category",
        "primary_metric": "krw_converted_amount",
        "suggested_columns": [
            "spend_category",
            "destination_country_name",
            "gender",
            "transaction_count",
            "total_krw_amount",
            "avg_krw_amount",
        ],
        "summary": f"{row_count} transaction rows loaded. Total KRW amount is {round(total_amount, 2)}.",
    }


def to_float(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return 0.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the automation supervisor flow without PostgreSQL.")
    parser.add_argument("--sample", choices=sorted(SAMPLES), default="travel")
    parser.add_argument("--requirement", help="Custom natural-language requirement.")
    parser.add_argument("--csv", help="Selected transaction CSV file path to summarize and pass to data processing.")
    parser.add_argument(
        "--feedback",
        default="선별된 데이터가 부족하고 테이블 선택이 요구사항과 맞지 않습니다.",
        help="Rejected HITL feedback used for rollback policy test.",
    )
    parser.add_argument(
        "--client",
        choices=["stub", "real"],
        default="stub",
        help="stub: 고정 응답. real: requirement-analysis/data-selection은 agent_runtime의 실제"
        " 에이전트를 호출(DeepSeek API 실사용, 시간/비용 발생). data-processing은 아직 실제"
        " 구현이 없어 실패가 예상됨.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main())
