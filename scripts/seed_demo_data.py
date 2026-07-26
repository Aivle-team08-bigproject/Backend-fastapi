"""로컬 데모용 요구사항과 원천 CSV를 서비스 DB에 적재한다.

실행:
    python -m scripts.seed_demo_data
    python -m scripts.seed_demo_data --data-dir ../dummyData
"""

import argparse
import asyncio
import csv
import hashlib
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.db.session import AsyncSessionLocal, engine, init_db
from app.domains.pipeline.model import (
    Client,
    DataRequest,
    DataRequestStatus,
    EventType,
    PipelineEvent,
    PipelineRun,
    PipelineRunStatus,
    SourceCard,
    SourceCustomer,
    SourceDataset,
    SourceMccCode,
    SourceMerchant,
    SourceTransaction,
    StageRun,
    StageRunStatus,
)


REQUESTS = [
    {
        "request_no": "REQ-20260714-001",
        "client": "정부 기관",
        "title": "특정 지역 인구 밀집도·시간대 분석 데이터",
        "purpose": "지역별 인구 밀집도·시간대 분석 (정책/행정 목적 추정)",
        "raw": "특정 지역의 인구 정보 데이터 (밀집 지역, 밀집 시간 등)",
        "source": "resident_address·merchant_address 지오코딩 + transaction_datetime 기반 지역·시간대별 결제 밀도 파생",
        "questions": ["대상 지역 범위(전국/특정 시군구)", "밀집 기준(연령/시간대 세분화 여부)", "재배포 조건"],
    },
    {
        "request_no": "REQ-20260714-002",
        "client": "부동산 관련 업체",
        "title": "지역별 가맹점 매출 기반 부동산 시세 분석 데이터",
        "purpose": "부동산 시세 분석/추정",
        "raw": "유동 인구 혹은 지역별 가맹점 매출을 통한 주변 부동산 시세 분석용 데이터",
        "source": "merchant_id 기준 transaction_amount 집계 + merchant_address 지오코딩으로 지역별 매출·유동인구 추정치 파생",
        "questions": ["분석 대상 지역", "집계 단위(동/상권 단위)", "시세 데이터 자체 보유 여부(연계 필요 시 외부 데이터 추가 확인)"],
    },
    {
        "request_no": "REQ-20260714-003",
        "client": "학원 (마케팅 목적)",
        "title": "주변 서점 이용률·이용자 연령대 마케팅 분석 데이터",
        "purpose": "학원 마케팅 전략 수립",
        "raw": "주변 서점 이용률, 이용자 연령대 분석 등 마케팅용 데이터",
        "source": "mcc_code(서점/학원 업종) 필터링 + birth_date 기반 연령 계산으로 이용 고객 연령 분포 파생",
        "questions": ["분석 대상 상권 범위", "서점/학원 외 추가 업종 포함 여부"],
    },
    {
        "request_no": "REQ-20260714-004",
        "client": "언론사 (경제 담당 기자)",
        "title": "경제 기획 기사용 소비 패턴 분석 데이터",
        "purpose": "경제 기획 기사 취재",
        "raw": "기획 기사 작성을 위한 경제 관련 데이터",
        "source": "annual_income_krw, occupation, resident_address 등을 조합한 소득/직업/지역별 소비 패턴 파생 (개인식별 불가 수준 비식별화 필수)",
        "questions": ["기사 주제(구체적 소재)", "비식별화 수준", "기사 게재 시 데이터 출처 표기 방식"],
    },
    {
        "request_no": "REQ-20260714-005",
        "client": "연구/모델링 목적 (신용카드 부정사용 방지)",
        "title": "신용카드 이상거래 탐지 모델 연구 데이터",
        "purpose": "AI 모델 학습 — 이상거래 탐지",
        "raw": "신용카드 부정 사용 방지 예측 모델 연구용 데이터 (이상 결제 탐지 관련)",
        "source": "decline_reason_code, auth_method, ip_address, pos_entry_mode, transaction_amount 시계열 패턴 등 raw 신호를 피처로 제공 (fraud_score 등 결과 레이블은 원본에 없음 — 모델이 직접 학습)",
        "questions": ["레이블링 데이터 별도 제공 필요 여부", "개인정보 비식별화 수준", "데이터 기간/샘플 규모"],
    },
]

DATASETS = (
    ("customers", "고객 기본 정보", SourceCustomer),
    ("cards", "카드 기본 정보", SourceCard),
    ("merchants", "가맹점 기본 정보", SourceMerchant),
    ("mcc_codes", "MCC 코드 참조", SourceMccCode),
    ("transactions", "카드 거래 내역", SourceTransaction),
)


def optional(row: dict[str, str], key: str) -> str | None:
    return row[key] or None


def dataset_file(data_dir: Path, code: str) -> Path:
    path = data_dir / f"{code}.csv"
    if not path.is_file():
        raise FileNotFoundError(f"원천 CSV를 찾을 수 없습니다: {path}")
    return path


def row_count(path: Path) -> int:
    with path.open(encoding="utf-8", newline="") as file:
        return sum(1 for _ in csv.DictReader(file))


def make_record(model: type, dataset_id: int, row: dict[str, str]):
    if model is SourceCustomer:
        return model(dataset_id=dataset_id, **row)
    if model is SourceCard:
        return model(dataset_id=dataset_id, **{key: optional(row, key) for key in row})
    if model is SourceMerchant:
        return model(dataset_id=dataset_id, **{key: optional(row, key) for key in row})
    if model is SourceMccCode:
        return model(dataset_id=dataset_id, **row)
    return model(
        dataset_id=dataset_id,
        transaction_id=row["transaction_id"],
        card_number_masked=row["card_number_masked"],
        merchant_id=row["merchant_id"],
        mcc_code=optional(row, "mcc_code"),
        transaction_datetime=datetime.fromisoformat(row["transaction_datetime"]),
        approval_status=row["approval_status"],
        decline_reason_code=optional(row, "decline_reason_code"),
        transaction_amount=Decimal(row["transaction_amount"]),
        currency_code=row["currency_code"],
        krw_converted_amount=Decimal(row["krw_converted_amount"]),
        applied_exchange_rate=Decimal(row["applied_exchange_rate"]),
        merchant_country_code=optional(row, "merchant_country_code"),
        installment_months=int(row["installment_months"]),
        approval_channel=optional(row, "approval_channel"),
        pos_entry_mode=optional(row, "pos_entry_mode"),
        auth_method=optional(row, "auth_method"),
        ip_address=optional(row, "ip_address"),
        device_id=optional(row, "device_id"),
        terminal_id=optional(row, "terminal_id"),
    )


async def seed_source_data(data_dir: Path) -> dict[str, int]:
    loaded: dict[str, int] = {}
    async with AsyncSessionLocal() as session:
        for code, display_name, model in DATASETS:
            path = dataset_file(data_dir, code)
            dataset = await session.scalar(select(SourceDataset).where(SourceDataset.dataset_code == code))
            if dataset is not None:
                loaded[code] = 0
                continue
            dataset = SourceDataset(
                dataset_code=code,
                display_name=display_name,
                source_file=str(path.resolve()),
                row_count=row_count(path),
                checksum_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            session.add(dataset)
            await session.flush()
            with path.open(encoding="utf-8", newline="") as file:
                records = [make_record(model, dataset.id, row) for row in csv.DictReader(file)]
            session.add_all(records)
            loaded[code] = len(records)
        await session.commit()
    return loaded


async def seed_requests() -> int:
    created = 0
    async with AsyncSessionLocal() as session:
        for item in REQUESTS:
            if await session.scalar(select(DataRequest.id).where(DataRequest.request_no == item["request_no"])):
                continue
            client = await session.scalar(select(Client).where(Client.company_name == item["client"]))
            if client is None:
                client = Client(company_name=item["client"], contact_email="demo-contact@example.invalid")
                session.add(client)
                await session.flush()
            request = DataRequest(
                request_no=item["request_no"],
                client_id=client.id,
                requester_name=item["client"],
                title=item["title"],
                business_purpose=item["purpose"],
                raw_requirement=item["raw"],
                output_formats=["CSV", "JSON"],
                delivery_channels=["FILE_DOWNLOAD"],
                analysis_condition={
                    "candidate_source": item["source"],
                    "redistribution_or_external_use": "확인 필요",
                    "commercial_service": "확인 필요",
                    "clarification_questions": item["questions"],
                },
                status=DataRequestStatus.WAITING_REVIEW,
                current_stage="REQUIREMENT_ANALYSIS",
            )
            session.add(request)
            await session.flush()
            run = PipelineRun(
                data_request_id=request.id,
                attempt_no=1,
                status=PipelineRunStatus.WAITING_REQUIREMENT_REVIEW,
                current_stage="REQUIREMENT_ANALYSIS",
                progress_percent=0,
            )
            session.add(run)
            await session.flush()
            stage = StageRun(
                pipeline_run_id=run.id,
                stage_code="REQUIREMENT_ANALYSIS",
                attempt_no=1,
                status=StageRunStatus.PENDING,
                input_payload={"request_no": request.request_no, "raw_requirement": request.raw_requirement},
            )
            session.add(stage)
            await session.flush()
            session.add(PipelineEvent(
                pipeline_run_id=run.id,
                stage_run_id=stage.id,
                event_type=EventType.PROGRESS,
                message="데모 요구사항이 등록되었습니다. 요구사항 분석 실행 또는 담당자 확인을 기다립니다.",
                payload={"request_no": request.request_no, "status": "PURPOSE_CONFIRMATION_PENDING"},
            ))
            created += 1
        await session.commit()
    return created


async def main(data_dir: Path) -> None:
    await init_db()
    loaded = await seed_source_data(data_dir)
    requests = await seed_requests()
    print(f"원천 데이터 적재: {loaded}; 데모 요구사항 생성: {requests}")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="데모 원천 데이터와 요구사항을 서비스 DB에 적재합니다.")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[2] / "dummyData")
    args = parser.parse_args()
    asyncio.run(main(args.data_dir))
