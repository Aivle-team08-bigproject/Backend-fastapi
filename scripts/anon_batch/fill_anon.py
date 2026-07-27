"""anon 스키마 값 채우기 배치 (mart → anon).

portfolio DB의 mart(가명정보)를 읽어 익명처리한 뒤 anon 테이블에 적재한다.
멱등(재실행 가능): 매 실행마다 anon을 TRUNCATE 후 다시 채운다. 주/일 배치로 운영.

계층 구분(금융분야 가명·익명처리 안내서 2022.01 기준):
  mart = 가명정보 — 추가정보 없이는 개인을 알아볼 수 없는 상태. 내부망에서만 취급.
  anon = 익명정보 — 더 이상 개인을 알아볼 수 없는 상태. LLM/AgentCore가 보는 유일한 계층.
  anon 데이터는 내부망 경계를 넘어 AgentCore로 전달되므로, 그 경계를 넘어도 되는
  수준까지 처리해야 한다.

실행 계정: portfolio_admin (= settings.portfolio_migration_database_url).
  mart SELECT + anon INSERT/TRUNCATE/TRIGGER 를 모두 하려면 세 스키마 소유자 권한이 필요하다.
  (agent_svc=anon 읽기전용, app_svc=mart 접근불가 이므로 둘 다 부적합)

실행:
    python scripts/anon_batch/fill_anon.py

의존성: psycopg (requirements.txt). pandas 불필요.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

import psycopg
from psycopg.types.json import Jsonb

# 레포 루트를 import 경로에 추가 (app.core.config 사용)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from app.core.config import settings  # noqa: E402

BATCH_VERSION = "anon-batch-v2"

K = 3
# k=3 근거: 본 데이터(800명) 실측 결과 k=3은 손실 12.8%, k=5는 38.1%로 과도하다.
#   안내서의 카드사 사례는 k=5를 쓰지만 그것은 "외부 일반사업자에게 제공"하는 환경이고,
#   안내서 자체가 "k값은 익명정보 이용 목적·환경 등에 따라 상이"하다고 명시한다.
#   데이터 규모가 커지면(2만 명 수준) 같은 손실률로 k=5 확보가 가능하므로 그때 재검토한다.
AGE_BANDS = ["10대", "20대", "30대", "40대", "50대", "60대", "70대 이상"]

# 시각 범주화 폭(시간). 안내서 카드사 사례의 "매출 발생 시각 8단계 범주화"에 대응한다.
# 문자열 구간이 아니라 3시간 경계로 절사해서 TIMESTAMPTZ 타입과 날짜를 유지한다.
# 실측: 심야 FDS 집중 신호(00~03시 15.2%, 03~06시 11.0% vs 09시 이후 0%)가 그대로 보존된다.
TIME_BUCKET_HOURS = 3


# =====================================================================
# 순수 변환 함수 (DB 무관)
# =====================================================================
def round_sig(x, sig: int = 2) -> Decimal:
    """유효숫자 sig자리 반올림. 다통화 안전(스케일 보존, 양수는 0이 되지 않음).

    안내서 카드사 사례의 "금액은 앞 2자리만 남기고 반올림"에 대응한다.
    """
    d = Decimal(x)
    if d == 0:
        return Decimal("0.00")
    q_exp = d.adjusted() - (sig - 1)
    return d.quantize(Decimal(1).scaleb(q_exp), rounding=ROUND_HALF_UP)


def recompute_krw(amount: Decimal, rate) -> Decimal:
    """krw = 반올림금액 × 환율 (ck_tx_krw_derived: 오차 ≤ 1 을 보장)."""
    return (Decimal(amount) * Decimal(rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def bucket_time(dt: datetime) -> datetime:
    """거래 시각을 TIME_BUCKET_HOURS 경계로 절사한다 (14:37 → 12:00).

    분·초 단위 정밀도는 그 자체가 특정 거래를 짚어내는 연결 키가 되므로 제거하고,
    시간대 패턴(심야 이상거래 등)은 구간으로 보존한다.
    """
    return dt.replace(hour=(dt.hour // TIME_BUCKET_HOURS) * TIME_BUCKET_HOURS,
                      minute=0, second=0, microsecond=0)


def device_band(count: int) -> str:
    return "1회" if count == 1 else ("2~5회" if count <= 5 else "6회 이상")


def terminal_band(count: int) -> str:
    return "1~5회" if count <= 5 else ("6~15회" if count <= 15 else "16회 이상")


def pseudonym(prefix: str, value: str) -> str:
    """결정적 가명 토큰. 동일 원본 → 동일 토큰(조인·집계 유지), 역산 불가.

    prefix로 도메인을 분리해서 같은 salt를 쓰더라도 토큰 공간이 겹치지 않게 한다
    (같은 원본 문자열이 고객ID로도 가맹점명으로도 등장할 때 같은 토큰이 나오면 안 됨).
    """
    h = hashlib.sha256(f"{settings.anon_hash_salt}|{prefix}|{value}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{h}"


def generalize_region(region: str) -> str:
    return region.split()[0]  # "서울특별시 송파구" -> "서울특별시"


def generalize_postal(postal: str) -> str:
    return postal[0] + "****"  # "032**" -> "0****" (region 정밀도와 일치)


# =====================================================================
# 로컬 억제 (customers, k=3)
# =====================================================================
def enforce_k(rows: list[dict], k: int = K):
    """rows: mart.customers dict 리스트. w_age/w_region/w_postal 작업필드를 채워 반환.
    반환: (rows, remaining_deficient). remaining이 비어있지 않으면 k 미달 잔존(중단 신호)."""
    for r in rows:
        r["w_age"] = r["age_band"]
        r["w_region"] = r["resident_region"]
        r["w_postal"] = r["postal_code"]

    # 2단계: 미달 행만 region/postal 동반 일반화
    # postal_code는 우편번호 앞자리가 자치구와 사실상 1:1이라, 지역만 뭉개고 두면
    # 우편번호로 역산이 가능해 일반화가 무의미해진다. 반드시 같은 단계에서 함께 처리한다.
    counts = Counter((r["gender"], r["w_age"], r["w_region"]) for r in rows)
    for r in rows:
        if counts[(r["gender"], r["w_age"], r["w_region"])] < k:
            r["w_region"] = generalize_region(r["w_region"])
            r["w_postal"] = generalize_postal(r["w_postal"])

    # 3단계: 반복 age 병합 (인접 "기존" 라벨로만, 새 라벨 생성 금지)
    # 새 라벨(예: "60~70대")을 만들면 그 라벨 자체가 "이 행은 억제 대상이었다"는
    # 단서가 되므로, 반드시 이미 존재하는 구간으로 흡수시킨다.
    while True:
        counts = Counter((r["gender"], r["w_age"], r["w_region"]) for r in rows)
        deficient = [key for key, c in counts.items() if c < k]
        if not deficient:
            break
        progressed = False
        for (g, age, region) in sorted(deficient, key=lambda kk: counts[kk]):
            if age not in AGE_BANDS:
                continue
            idx = AGE_BANDS.index(age)
            present = {r["w_age"] for r in rows if r["gender"] == g and r["w_region"] == region}
            neighbors = []
            for dist in range(1, len(AGE_BANDS)):
                for j in (idx - dist, idx + dist):
                    if 0 <= j < len(AGE_BANDS) and AGE_BANDS[j] in present and AGE_BANDS[j] != age:
                        neighbors.append(AGE_BANDS[j])
                if neighbors:
                    break
            if not neighbors:
                continue
            # 병합 후 그룹이 가장 커지는 이웃, 동점이면 더 젊은 쪽 — 결정적
            target = max(neighbors, key=lambda nb: (counts.get((g, nb, region), 0), -AGE_BANDS.index(nb)))
            for r in rows:
                if r["gender"] == g and r["w_age"] == age and r["w_region"] == region:
                    r["w_age"] = target
            progressed = True
            break  # 병합 1회마다 재계산
        if not progressed:
            # age 병합으로 해소 불가 → gender를 자동 처리하지 않고 중단(사람 판단)
            return rows, deficient
    return rows, []


# =====================================================================
# 테이블별 적재
# =====================================================================
def fetch_all(cur, table: str, cols: list[str]) -> list[dict]:
    cur.execute(f"SELECT {', '.join(cols)} FROM {table}")
    return [dict(zip(cols, row)) for row in cur.fetchall()]


MCC_COLS = ["mcc_code", "mcc_name"]
CUST_COLS = ["customer_id", "gender", "age_band", "resident_region", "postal_code",
             "occupation", "annual_income_band", "marital_status"]
CARD_COLS = ["card_number_masked", "customer_id", "card_product_code", "card_issue_month",
             "credit_limit_band", "card_status", "card_status_changed_month", "signup_channel"]
MERCH_COLS = ["merchant_id", "merchant_name", "business_registration_number", "mcc_code",
              "franchise_hq_code", "merchant_region", "merchant_open_month", "fee_tier_code",
              "merchant_status", "merchant_status_changed_month"]
TX_COLS = ["transaction_id", "card_number_masked", "merchant_id", "mcc_code", "transaction_datetime",
           "approval_status", "decline_reason_code", "transaction_amount", "currency_code",
           "krw_converted_amount", "applied_exchange_rate", "merchant_country_code",
           "installment_months", "approval_channel", "pos_entry_mode", "auth_method",
           "ip_address", "device_id", "terminal_id"]
TX_INSERT_COLS = TX_COLS[:-2] + ["device_frequency_band", "terminal_frequency_band"]


def insert_many(cur, table: str, cols: list[str], rows: list[tuple]):
    placeholders = ", ".join(["%s"] * len(cols))
    cur.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})", rows)


def run(cur) -> dict:
    """mart를 읽어 anon에 적재하고 처리 통계를 반환한다."""
    # ---- mcc_codes: 그대로 복제 (개인정보 아님) ----
    mcc = fetch_all(cur, "mart.mcc_codes", MCC_COLS)
    insert_many(cur, "anon.mcc_codes", MCC_COLS, [(m["mcc_code"], m["mcc_name"]) for m in mcc])

    # ---- customers: 식별자 재토큰화 + 로컬 억제 ----
    cust = fetch_all(cur, "mart.customers", CUST_COLS)
    cust, remaining = enforce_k(cust)
    if remaining:
        raise SystemExit(f"[중단] k={K} 미달 그룹이 age 병합으로 해소되지 않음(성별 처리 필요): {remaining}")

    # 식별자 재토큰화: mart의 customer_id를 그대로 두면 두 계층을 잇는 결합 키가 남아
    # "다른 정보와 결합해도 개인을 알아볼 수 없어야 한다"는 익명정보 요건이 흔들린다.
    # 안내서 III.2.나: "익명처리시 식별자는 삭제하여야 하며, 부득이하게 정보 이용
    # 목적상 필요한 경우에는 적절하게 익명처리를 한 후 이용하여야 한다."
    # → 고객 단위 집계가 필요하므로 삭제 대신 별도 토큰으로 재생성한다.
    id_map = {c["customer_id"]: pseudonym("ACUST", c["customer_id"]) for c in cust}

    suppressed = sum(1 for c in cust if c["w_region"] != c["resident_region"])
    age_merged = sum(1 for c in cust if c["w_age"] != c["age_band"])

    cust_rows = [(id_map[c["customer_id"]], c["gender"], c["w_age"], c["w_region"], c["w_postal"],
                  c["occupation"], c["annual_income_band"], c["marital_status"]) for c in cust]
    insert_many(cur, "anon.customers", CUST_COLS, cust_rows)

    # ---- cards: customer_id만 재토큰화, 나머지는 복제 ----
    cards = fetch_all(cur, "mart.cards", CARD_COLS)
    card_rows = []
    for c in cards:
        row = dict(c)
        row["customer_id"] = id_map[c["customer_id"]]  # FK 정합 유지
        card_rows.append(tuple(row[k] for k in CARD_COLS))
    insert_many(cur, "anon.cards", CARD_COLS, card_rows)

    # ---- merchants: 상호명·사업자번호 가명화 ----
    # 개인사업자의 상호에는 대표자명이 포함될 수 있고, 사업자등록번호는 개인 식별로
    # 이어질 수 있다. merchant_id는 조인 유지를 위해 그대로 둔다(이미 토큰).
    merch = fetch_all(cur, "mart.merchants", MERCH_COLS)
    merch_rows = []
    for m in merch:
        row = dict(m)
        row["merchant_name"] = pseudonym("MERCH", m["merchant_name"])
        row["business_registration_number"] = pseudonym("BRN", m["business_registration_number"])
        merch_rows.append(tuple(row[k] for k in MERCH_COLS))
    insert_many(cur, "anon.merchants", MERCH_COLS, merch_rows)

    # ---- transactions: 금액·시각 정밀도 축소 + 기기/단말 빈도 구간화 ----
    tx = fetch_all(cur, "mart.transactions", TX_COLS)
    dev_freq = Counter(t["device_id"] for t in tx if t["device_id"] is not None)
    term_freq = Counter(t["terminal_id"] for t in tx if t["terminal_id"] is not None)
    tx_rows = []
    for t in tx:
        amount = round_sig(t["transaction_amount"])
        krw = recompute_krw(amount, t["applied_exchange_rate"])
        dt = bucket_time(t["transaction_datetime"])
        # device_id/terminal_id는 "같은 기기의 반복 사용"이라는 연결 자체가 존재 목적이라
        # 준식별자처럼 단계적으로 일반화할 수 없다. 원본 토큰 대신 등장 빈도 구간으로
        # 대체해서 부정거래 신호는 남기고 개별 기기 추적은 차단한다.
        dband = device_band(dev_freq[t["device_id"]]) if t["device_id"] is not None else None
        tband = terminal_band(term_freq[t["terminal_id"]])
        tx_rows.append((
            t["transaction_id"], t["card_number_masked"], t["merchant_id"], t["mcc_code"], dt,
            t["approval_status"], t["decline_reason_code"], amount, t["currency_code"], krw,
            t["applied_exchange_rate"], t["merchant_country_code"], t["installment_months"],
            t["approval_channel"], t["pos_entry_mode"], t["auth_method"], t["ip_address"],
            dband, tband,
        ))
    insert_many(cur, "anon.transactions", TX_INSERT_COLS, tx_rows)

    return {
        "row_counts": {"mcc_codes": len(mcc), "customers": len(cust), "cards": len(cards),
                       "merchants": len(merch), "transactions": len(tx)},
        "suppressed_rows": suppressed,
        "age_merged_rows": age_merged,
    }


# =====================================================================
# 검증 (적재 후, 트리거 재활성 상태에서 세트 기반)
# =====================================================================
VALIDATIONS = [
    ("행수 1:1 mcc", "SELECT (SELECT count(*) FROM anon.mcc_codes)<>(SELECT count(*) FROM mart.mcc_codes)"),
    ("행수 1:1 customers", "SELECT (SELECT count(*) FROM anon.customers)<>(SELECT count(*) FROM mart.customers)"),
    ("행수 1:1 cards", "SELECT (SELECT count(*) FROM anon.cards)<>(SELECT count(*) FROM mart.cards)"),
    ("행수 1:1 merchants", "SELECT (SELECT count(*) FROM anon.merchants)<>(SELECT count(*) FROM mart.merchants)"),
    ("행수 1:1 transactions", "SELECT (SELECT count(*) FROM anon.transactions)<>(SELECT count(*) FROM mart.transactions)"),
    ("k 미달 그룹",
     "SELECT count(*) FROM (SELECT 1 FROM anon.customers "
     "GROUP BY gender, age_band, resident_region HAVING count(*) < %d) q" % K),
    ("고객 식별자 미변환(mart와 동일)",
     "SELECT count(*) FROM anon.customers a JOIN mart.customers m USING (customer_id)"),
    ("시각 범주화 위반", "SELECT count(*) FROM anon.transactions WHERE "
     "extract(minute FROM transaction_datetime)<>0 OR extract(second FROM transaction_datetime)<>0 "
     "OR (extract(hour FROM transaction_datetime)::int %% %d)<>0" % TIME_BUCKET_HOURS),
    ("krw 파생오차>1", "SELECT count(*) FROM anon.transactions "
     "WHERE abs(krw_converted_amount - transaction_amount*applied_exchange_rate) > 1"),
    ("amount<=0", "SELECT count(*) FROM anon.transactions WHERE transaction_amount<=0 OR krw_converted_amount<=0"),
    ("ip/device 짝 위반", "SELECT count(*) FROM anon.transactions "
     "WHERE (ip_address IS NULL) <> (device_frequency_band IS NULL)"),
    ("가맹점 BRN 미가명화",
     "SELECT count(*) FROM anon.merchants a JOIN mart.merchants m USING (merchant_id) "
     "WHERE a.business_registration_number = m.business_registration_number"),
    ("카드-고객 FK 고아",
     "SELECT count(*) FROM anon.cards c LEFT JOIN anon.customers u USING (customer_id) "
     "WHERE u.customer_id IS NULL"),
]


def validate(cur) -> tuple[bool, list[str]]:
    print("\n=== 검증 ===")
    ok = True
    failures = []
    for desc, q in VALIDATIONS:
        cur.execute(q)
        v = cur.fetchone()[0]
        bad = bool(v)
        if bad:
            ok = False
            failures.append(f"{desc}={v}")
        print(f"  [{'FAIL' if bad else 'PASS'}] {desc}: {v}")
    return ok, failures


# =====================================================================
# 처리 이력 기록 (안내서 기록보존 의무 — 3년)
# =====================================================================
TARGET_ITEMS = {
    "customers": ["customer_id", "age_band", "resident_region", "postal_code"],
    "cards": ["customer_id"],
    "merchants": ["merchant_name", "business_registration_number"],
    "transactions": ["transaction_datetime", "transaction_amount", "krw_converted_amount",
                     "device_id", "terminal_id"],
}
TECHNIQUES = {
    "식별자": "결정적 토큰 재생성(salt 기반, 역산 불가)",
    "준식별자": f"k-익명성 k={K} 로컬 억제 — 지역 일반화 후 연령 인접구간 병합",
    "우편번호": "거주지역과 동반 일반화",
    "거래시각": f"{TIME_BUCKET_HOURS}시간 구간 절사",
    "거래금액": "유효숫자 2자리 반올림(환산액 재계산)",
    "기기·단말": "등장 빈도 구간화",
    "가맹점": "상호명·사업자등록번호 토큰 치환",
}
PURPOSE = "AI 에이전트 기반 맞춤형 데이터 가공 서비스 제공 — 내부망 외부(AgentCore)로 전달되는 분석용 데이터 생성"
LEGAL_BASIS = "신용정보법 제2조제17호(익명처리) / 금융분야 가명·익명처리 안내서(2022.01) III. 익명처리"


def write_log(dsn: str, stats: dict | None, passed: bool, error: str | None):
    """익명처리 이력을 남긴다. 본 작업과 별도 트랜잭션이므로 실패한 실행도 기록된다."""
    started = datetime.now().astimezone()
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mart.anonymization_log
                    (executed_at, completed_at, target_items, purpose, legal_basis,
                     techniques, k_value, suppressed_rows, row_counts,
                     validation_passed, error_message, batch_version)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (started, started, Jsonb(TARGET_ITEMS), PURPOSE, LEGAL_BASIS,
                 Jsonb(TECHNIQUES), K, (stats or {}).get("suppressed_rows", 0),
                 Jsonb((stats or {}).get("row_counts", {})), passed, error, BATCH_VERSION),
            )
        conn.commit()


# =====================================================================
# 메인 — 멱등 적재
# =====================================================================
def main():
    if not settings.anon_hash_salt:
        raise SystemExit("ANON_HASH_SALT 가 .env 에 설정돼 있어야 합니다 (식별자·가맹점 가명화 salt).")

    dsn = settings.portfolio_migration_database_url.replace("+psycopg", "")
    stats = None
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # 대량 적재 동안 행 단위 정합성 트리거를 끄고, 적재 후 세트 기반으로 검증한다.
                cur.execute("ALTER TABLE anon.transactions DISABLE TRIGGER USER")
                cur.execute("TRUNCATE anon.transactions, anon.merchants, anon.cards, "
                            "anon.customers, anon.mcc_codes")
                stats = run(cur)
                cur.execute("ALTER TABLE anon.transactions ENABLE TRIGGER USER")
                ok, failures = validate(cur)
            if ok:
                conn.commit()
            else:
                conn.rollback()
                write_log(dsn, stats, False, "; ".join(failures))
                raise SystemExit("\n❌ 검증 실패 → 롤백. anon은 변경되지 않았습니다.")
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — 실패도 이력에 남겨야 한다
        write_log(dsn, stats, False, str(exc))
        raise

    write_log(dsn, stats, True, None)
    print(f"\n✅ 커밋 완료: {stats['row_counts']}")
    print(f"   로컬 억제로 일반화된 행: {stats['suppressed_rows']}건 "
          f"({stats['suppressed_rows'] / max(stats['row_counts']['customers'], 1) * 100:.1f}%)"
          f" / 연령 병합: {stats['age_merged_rows']}건")
    print("   처리 이력이 mart.anonymization_log에 기록되었습니다(보존 3년).")


if __name__ == "__main__":
    main()