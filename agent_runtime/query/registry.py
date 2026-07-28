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


metadata = MetaData(schema="anon")

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
    "merchant": Dataset(
        "merchant",
        merchants,
        ("merchant_id", "mcc_code", "merchant_region", "fee_tier_code", "merchant_status"),
        frozenset({"merchant_name", "business_registration_number"}),
    ),
    "member_pseudonymized": Dataset(
        "member_pseudonymized",
        customers,
        ("customer_id", "gender", "age_band", "resident_region", "occupation"),
        frozenset({"postal_code"}),
    ),
    "transaction_pseudonymized": Dataset(
        "transaction_pseudonymized",
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
