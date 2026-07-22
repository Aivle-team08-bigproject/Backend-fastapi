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

VERBOSE = False


async def main() -> None:
    global VERBOSE
    args = parse_args()
    VERBOSE = args.verbose
    raw_requirement = args.requirement or SAMPLES[args.sample]
    # --client real calls all three agent_runtime stages. Data processing requires --csv because
    # data selection currently creates a query plan rather than fetching actual database rows.
    client = StrandsAgentClient() if args.client == "real" else StubAgentClient()

    print("RAW_REQUIREMENT")
    print(raw_requirement)

    analysis = await client.run(
        "requirement-analysis-agent",
        "sonnet-4.6",
        {"raw_requirement": raw_requirement},
    )
    analysis_validation = print_result("REQUIREMENT_ANALYSIS", analysis)
    if not analysis_validation["passed"]:
        print("\nPIPELINE_STOPPED: 요구사항 분석 실패로 이후 단계를 실행하지 않습니다.")
        return

    selection = await client.run(
        "data-selection-agent",
        "aws-nova",
        {
            "raw_requirement": raw_requirement,
            "analysis": analysis,
            "available_data": ["merchant", "member_pseudonymized", "transaction_pseudonymized"],
        },
    )
    selection_validation = print_result("DATA_SELECTION", selection)
    if not selection_validation["passed"]:
        print("\nPIPELINE_STOPPED: 데이터 선별 실패로 이후 단계를 실행하지 않습니다.")
        return

    selected_rows = read_csv_rows(args.csv) if args.csv else []
    selected_data_summary = summarize_rows(selected_rows, args.csv) if selected_rows else {}
    if selected_data_summary:
        print("SELECTED_DATA_SUMMARY")
        print(json.dumps(selected_data_summary, ensure_ascii=False, indent=2))

    processing = await client.run(
        "data-processing-agent",
        "deterministic-python-v1",
        {
            "raw_requirement": raw_requirement,
            "analysis": analysis,
            "selection": selection,
            "selected_rows": selected_rows,
            "selected_data_summary": selected_data_summary,
        },
    )
    processing_validation = print_result("DATA_PROCESSING", processing)
    if not processing_validation["passed"]:
        print("\nPIPELINE_STOPPED: 데이터 가공 실패로 HITL 단계를 실행하지 않습니다.")
        return

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
    print(
        json.dumps(
            {"failure_code": failure_code.value, "rollback_to": rollback_stage.value},
            ensure_ascii=False,
            indent=2,
        )
    )


def print_result(stage_name: str, artifact: dict) -> dict:
    validation = validate_stage_output(StageName(stage_name), artifact)
    print(f"\n[{stage_name}]")
    output = artifact if VERBOSE else summarize_artifact(stage_name, artifact)
    print(json.dumps({"validation": validation, "artifact": output}, ensure_ascii=False, indent=2))
    return validation


def summarize_artifact(stage_name: str, artifact: dict) -> dict:
    """Return a developer-readable summary without raw rows or Base64 content."""
    if artifact.get("_agent_error"):
        return {
            "agent_error": artifact.get("_agent_error"),
            "failure_code": artifact.get("_failure_code"),
        }

    if stage_name == "REQUIREMENT_ANALYSIS":
        return {
            "usage_purpose": artifact.get("usage_purpose"),
            "requested_data_sentence": artifact.get("requested_data_sentence"),
            "categories": artifact.get("categories"),
            "delivery_channel": artifact.get("delivery_channel"),
            "output_formats": artifact.get("output_formats"),
        }

    if stage_name == "DATA_SELECTION":
        query = artifact.get("selection_query") or {}
        return {
            "selected_tables": [
                {"table": item.get("table"), "reason": item.get("reason")}
                for item in artifact.get("selected_tables", [])
            ],
            "top_k": query.get("top_k"),
            "filters": query.get("filters"),
            "sample_columns": [
                {
                    "name": item.get("name"),
                    "is_predicted": item.get("is_predicted"),
                    "description": item.get("description"),
                }
                for item in artifact.get("sample_columns", [])
            ],
        }

    if stage_name == "DATA_PROCESSING":
        quality = artifact.get("quality_report") or {}
        csv_artifact = artifact.get("csv_artifact") or {}
        api_result = artifact.get("api_result") or {}
        visualization = artifact.get("visualization") or {}
        report = artifact.get("report") or {}
        explanation = artifact.get("processing_explanation") or {}
        return {
            "processing_engine": artifact.get("processing_engine"),
            "processed_columns": artifact.get("processed_columns"),
            "rows": {
                "input": quality.get("input_row_count"),
                "output": quality.get("output_row_count"),
                "duplicates_removed": quality.get("duplicates_removed"),
                "imputed": quality.get("imputation_count"),
            },
            "missing_after": {
                key: value for key, value in (quality.get("missing_after") or {}).items() if value
            },
            "anonymization": explanation.get("anonymization", []),
            "processing_explanation": {
                "summary": explanation.get("summary"),
                "missing_value_handling": explanation.get("missing_value_handling", []),
                "format_conversion": explanation.get("format_conversion", {}),
                "duplicate_handling": explanation.get("duplicate_handling", {}),
                "safeguards": explanation.get("safeguards", []),
            },
            "csv": {
                "generated": bool(csv_artifact),
                "encoding": csv_artifact.get("encoding"),
                "byte_size": csv_artifact.get("byte_size"),
                "sha256": csv_artifact.get("sha256"),
            },
            "api_item_count": len(api_result.get("items") or []),
            "visualization": {
                "generated": bool(visualization),
                "chart_type": visualization.get("chart_type"),
                "x": visualization.get("x"),
                "y": visualization.get("y"),
            },
            "report": {
                "generated": bool(report),
                "title": report.get("title"),
                "summary": report.get("summary"),
            },
        }

    if stage_name == "HITL_REVIEW":
        return {
            "approved": artifact.get("approved"),
            "reviewer": artifact.get("reviewer"),
            "failure_code": artifact.get("failure_code"),
            "feedback": artifact.get("natural_feedback"),
        }

    return artifact


def read_csv_rows(csv_path: str) -> list[dict]:
    path = Path(csv_path)
    encodings = ["utf-8-sig", "utf-8", "cp949"]
    last_error: Exception | None = None

    for encoding in encodings:
        try:
            with path.open("r", encoding=encoding, newline="") as file:
                return list(csv.DictReader(file))
        except UnicodeDecodeError as exc:
            last_error = exc
    else:
        raise RuntimeError(f"CSV encoding failed: {last_error}")


def summarize_rows(rows: list[dict], csv_path: str) -> dict:
    path = Path(csv_path)

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
        help="stub: 고정 응답. real: agent_runtime의 실제 세 단계를 호출하며 앞의 두 단계는 "
        "DeepSeek API를 사용합니다. data-processing에는 --csv가 필요합니다.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Base64 CSV와 전체 API 행을 포함한 원본 산출물 JSON을 출력합니다.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main())
