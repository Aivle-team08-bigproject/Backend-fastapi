from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    String,
    Table,
)

import os


metadata = MetaData(schema="anonymized")
 
# 업종 코드 마스터. 개인정보가 아니므로 차단 대상이 아니다.
# 이 테이블이 없으면 LLM은 mcc_code 숫자만 받고 "음식점" 같은 업종명을 얻지 못한다.
mcc_codes = Table(
    "mcc_codes",
    metadata,
    Column("mcc_code", Integer),
    Column("mcc_name", String),
)

customers = Table(
    "customers",
    metadata,
    Column("customer_id", String),
    Column("gender", String),
    Column("age_band", String),
    Column("resident_region", String),
    Column("postal_code", String),
    Column("occupation", String),
    Column("annual_income_band", String),
    Column("marital_status", String),
)
cards = Table(
    "cards",
    metadata,
    Column("card_number_masked", String),
    Column("customer_id", String),
    Column("card_product_code", String),
    Column("card_issue_month", String),
    Column("credit_limit_band", String),
    Column("card_status", String),
    Column("card_status_changed_month", String),
    Column("signup_channel", String),
)
merchants = Table(
    "merchants",
    metadata,
    Column("merchant_id", String),
    Column("merchant_name", String),
    Column("business_registration_number", String),
    Column("mcc_code", Integer),
    Column("franchise_hq_code", String),
    Column("merchant_region", String),
    Column("merchant_open_month", String),
    Column("fee_tier_code", String),
    Column("merchant_status", String),
    Column("merchant_status_changed_month", String),
)
transactions = Table(
    "transactions",
    metadata,
    Column("transaction_id", String),
    Column("card_number_masked", String),
    Column("merchant_id", String),
    Column("mcc_code", Integer),
    Column("transaction_datetime", DateTime(timezone=True)),
    Column("approval_status", String),
    Column("decline_reason_code", String),
    Column("transaction_amount", Numeric),
    Column("currency_code", String),
    Column("krw_converted_amount", Numeric),
    Column("applied_exchange_rate", Numeric),
    Column("merchant_country_code", String),
    Column("installment_months", SmallInteger),
    Column("approval_channel", String),
    Column("pos_entry_mode", String),
    Column("auth_method", String),
    Column("ip_address", String),
    Column("device_frequency_band", String),
    Column("terminal_frequency_band", String),
)


# ---------------------------------------------------------------------
# 재식별 방지 정책
# ---------------------------------------------------------------------
# anonymized 계층은 준식별자(성별·연령대·거주지역) 조합에 최소 K명이 있도록 만들어졌다.
# 이 보장은 "조합별 인원이 K 이상"이라는 뜻이지 "그 조합을 원시 행으로 뽑아도
# 된다"는 뜻이 아니다. 조합을 그대로 반환하면 K는 사실상 무의미해진다.
#
#   규칙 A (plan.py)      인적 속성 2개 이상 + 개별 식별자 동시 선택 금지
#   규칙 B (executors.py) 인적 속성 2개 이상이면, 출력에 포함된 차원 조합에
#                         서로 다른 고객이 K명 이상 있어야 그 행을 반환한다
#
# 산출물이 어디로 나가든(외부 S3 포함) 안전하려면 반출 시점이 아니라 조회
# 시점에 막아야 한다. 차트 이미지 안의 숫자는 사후 검사가 불가능하기 때문이다.
#
# 이 규칙은 DLP 와 다르다. DLP 는 값 자체가 위험한 것(주민번호 형태의 문자열)을
# 패턴으로 잡고, 여기서는 개별 값이 전부 무해한데 조합하면 개인이 특정되는 것을
# 막는다. 어느 쪽도 다른 쪽을 대체하지 못한다.

# K는 익명화 배치(scripts/anon_batch/fill_anon.py)의 값과 반드시 일치시킨다.
# 데이터 규모가 커지면 상향할 수 있도록 환경변수로 뺀다.
K_ANONYMITY = max(5, int(os.getenv("QUERY_K_ANONYMITY", "5")))

# 조합될수록 개인 특정에 가까워지는 인적 속성.
PERSON_ATTRIBUTES = frozenset({
    "gender", "age_band", "resident_region", "postal_code",
    "occupation", "annual_income_band", "marital_status",
})

# 행 하나 또는 개체 하나를 그대로 가리키는 값.
# 인적 속성과 함께 뽑으면 그룹 크기가 항상 1이 되어 규칙 B가 무력화된다.
#
# 고차원 컬럼을 여기 넣는 이유(2026-07-31 실측):
#   성별·연령·지역·업종 4차원 기준 통과율 82.3%(10,269/12,477행)인데,
#   여기에 컬럼 하나를 더하면 이렇게 된다.
#       transaction_datetime(2,730종)  통과 0행      전면 차단
#       card_issue_month(120종)        통과 13행     0.1%
#       merchant_open_month(100종)     같은 성격
#       franchise_hq_code(31종)        통과 7,415행  59.4%
#   차원으로 두면 규칙 B가 조용히 빈 결과를 돌려주고, 에이전트는 그것을
#   "데이터가 없다"로 잘못 해석한다. 식별자로 두면 규칙 A가 이유를 담은
#   오류를 던져 다른 조합을 시도할 수 있다.
#
#   이 문제는 데이터가 늘어도 해결되지 않는다. 저차원 조합은 고객 수가 늘면
#   셀당 인원이 늘어 자연히 해소되지만(10만 명이면 셀당 29.8명), 시간축처럼
#   기간에 비례해 값이 늘어나는 컬럼은 셀 수가 함께 자란다. 그리고 필터를
#   좁히면 전체 규모와 무관하게 소수 집단이 다시 나타난다.
ROW_IDENTIFIERS = frozenset({
    # 사람·카드·거래를 직접 가리킨다
    "customer_id", "card_number_masked", "transaction_id",
    # 가맹점 하나를 지목한다. 셋은 서로 1:1이라 하나만 막으면 우회된다.
    "merchant_id", "merchant_name", "business_registration_number",
    # 기기·회선 단위 추적이 가능하다
    "ip_address",
    # 고차원 — 위 실측 참고
    "transaction_datetime", "card_issue_month",
    "merchant_open_month", "franchise_hq_code",
})

# 그룹 크기를 판정할 때 쓰는 맥락 차원. 값 종류가 적은 범주형만 넣는다.
# 값 종류가 30을 넘으면 조합이 잘게 쪼개져 통과율이 급락하므로 식별자로 옮긴다.
CONTEXT_DIMENSIONS = frozenset({
    # 카드 (5종 이하)
    "card_product_code", "credit_limit_band", "card_status",
    "card_status_changed_month", "signup_channel",
    # 가맹점 (15종 이하)
    "mcc_code", "mcc_name", "merchant_region",
    "fee_tier_code", "merchant_status", "merchant_status_changed_month",
    # 거래 (9종 이하)
    "approval_status", "decline_reason_code", "currency_code",
    "merchant_country_code", "installment_months", "approval_channel",
    "pos_entry_mode", "auth_method",
    "device_frequency_band", "terminal_frequency_band",
})

# 규칙 B가 그룹을 나누는 기준. 출력에 포함된 이 컬럼들의 조합마다
# 서로 다른 고객이 K명 이상이어야 한다.
QUASI_IDENTIFIERS = PERSON_ATTRIBUTES | CONTEXT_DIMENSIONS

# 연속값. GROUP BY에 넣으면 조합이 모두 1이 되어 전부 차단되므로 제외한다.
MEASURE_COLUMNS = frozenset({
    "transaction_amount", "krw_converted_amount", "applied_exchange_rate",
})


@dataclass(frozen=True)
class Dataset:
    logical_name: str
    table: Table
    default_columns: tuple[str, ...]
    blocked_columns: frozenset[str] = frozenset()

    @property
    def allowed_columns(self) -> frozenset[str]:
        return frozenset(self.table.c.keys()) - self.blocked_columns


DATASETS = {
    "anon_mcc_codes": Dataset(
        "anon_mcc_codes",
        mcc_codes,
        ("mcc_code", "mcc_name"),
    ),
    "anon_merchants": Dataset(
        "anon_merchants",
        merchants,
        ("merchant_id", "mcc_code", "merchant_region", "fee_tier_code", "merchant_status"),
        # anonymized의 상호명·사업자번호는 이미 대체값이라 식별에 쓸 수 없다.
        # 다만 분석에도 쓸모가 없어(브랜드를 유추할 수 없음) 노출하지 않는다.
        frozenset({"merchant_name", "business_registration_number"}),
    ),
    "anon_customers": Dataset(
        "anon_customers",
        customers,
        # 기본값에서 준식별자 조합을 뺐다. 조합 분석이 필요하면 LLM이 명시적으로
        # 요청해야 하고, 그때는 규칙 A/B가 적용된다.
        ("customer_id", "age_band"),
        frozenset({"postal_code"}),
    ),
    "anon_transactions": Dataset(
        "anon_transactions",
        transactions,
        (
            "transaction_id",
            "merchant_id",
            "mcc_code",
            "transaction_datetime",
            "approval_status",
            "transaction_amount",
            "currency_code",
            "krw_converted_amount",
            "merchant_country_code",
        ),
        frozenset({"card_number_masked", "ip_address"}),
    ),
}

# 이전 논리명 호환.
# 이 계층은 anonymized(익명)인데 이름이 pseudonymized(가명)라 개념이 반대로 읽힌다.
# 정식 이름을 anon_*로 두고 옛 이름은 별칭으로만 남긴다 — 논리명은 LLM
# 프롬프트에 그대로 들어가므로, 이름이 계층을 잘못 알려주면 안 된다.
DATASET_ALIASES = {
    "merchant": "anon_merchants",
    "member_pseudonymized": "anon_customers",
    "transaction_pseudonymized": "anon_transactions",
    "mcc": "anon_mcc_codes",
    "mcc_codes": "anon_mcc_codes",
}


def canonical_dataset(name: str) -> str:
    normalized = str(name).strip()
    return DATASET_ALIASES.get(normalized, normalized)

COLUMN_ALIASES = {
    "성별": "gender",
    "연령": "age_band",
    "연령대": "age_band",
    "나이": "age_band",
    "거주지역": "resident_region",
    "지역": "merchant_region",
    "가맹점지역": "merchant_region",
    "업종": "mcc_code",
    "업종코드": "mcc_code",
    "결제금액": "transaction_amount",
    "거래금액": "transaction_amount",
    "승인상태": "approval_status",
    "국가": "merchant_country_code",
}

AMBIGUOUS_COLUMN_ALIASES = {
    "지역": ("merchant_region", "resident_region", "region"),
    "결제금액": ("transaction_amount", "krw_converted_amount", "amount"),
    "거래금액": ("transaction_amount", "krw_converted_amount", "amount"),
}


def canonical_column(name: str) -> str:
    return COLUMN_ALIASES.get(name.strip(), name.strip())


def resolve_column(name: str, allowed: set[str]) -> str:
    normalized = name.strip()
    candidates = AMBIGUOUS_COLUMN_ALIASES.get(
        normalized,
        (canonical_column(normalized), normalized),
    )
    matches = [candidate for candidate in candidates if candidate in allowed]
    if not matches:
        return canonical_column(normalized)
    return matches[0]
