"""anon 스키마 값 채우기 배치 (mart → anon).

hanacard DB의 mart(가명데이터)를 읽어 k=3 익명화한 뒤 anon 테이블에 적재한다.
멱등(재실행 가능): 매 실행마다 anon을 TRUNCATE 후 다시 채운다. 주/일 배치로 운영.

실행 계정: hanacard_admin (= settings.hanacard_migration_database_url).
  mart SELECT + anon INSERT/TRUNCATE/TRIGGER 를 모두 하려면 세 스키마 소유자 권한이 필요하다.
  (agent_svc=anon 읽기전용, app_svc=mart 접근불가 이므로 둘 다 부적합. 배포 시 전용 최소권한 역할 신설 예정.)

실행:
    # 레포 루트에서, backend-fastapi 가상환경 활성화 상태로
    ANON_HASH_SALT=... python scripts/anon_batch/fill_anon.py
    (또는 .env 에 ANON_HASH_SALT / HANACARD_MIGRATION_DATABASE_URL 설정 후 그냥 실행)

의존성: psycopg (이미 requirements.txt 에 psycopg[binary]). pandas 불필요.
"""

from __future__ import annotations

import hashlib
import os
import sys
from collections import Counter
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

import psycopg

# 레포 루트를 import 경로에 추가 (app.core.config 사용)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from app.core.config import settings  # noqa: E402

K = 3
AGE_BANDS = ["10대", "20대", "30대", "40대", "50대", "60대", "70대 이상"]


# =====================================================================
# 순수 변환 함수 (DB 무관, 단위 검증 완료)
# =====================================================================
def round_sig(x, sig: int = 2) -> Decimal:
    """유효숫자 sig자리 반올림. 다통화 안전(스케일 보존, 양수는 0이 되지 않음)."""
    d = Decimal(x)
    if d == 0:
        return Decimal("0.00")
    q_exp = d.adjusted() - (sig - 1)
    return d.quantize(Decimal(1).scaleb(q_exp), rounding=ROUND_HALF_UP)


def recompute_krw(amount: Decimal, rate) -> Decimal:
    """krw = 반올림금액 × 환율 (ck_tx_krw_derived: 오차 ≤ 1 을 보장)."""
    return (Decimal(amount) * Decimal(rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def trunc_hour(dt: datetime) -> datetime:
    """시각 시(hour) 단위 절사 (tz 유지). 새벽·시간대 FDS 신호는 보존, 분·초 linkage 제거."""
    return dt.replace(minute=0, second=0, microsecond=0)


def device_band(count: int) -> str:
    return "1회" if count == 1 else ("2~5회" if count <= 5 else "6회 이상")


def terminal_band(count: int) -> str:
    return "1~5회" if count <= 5 else ("6~15회" if count <= 15 else "16회 이상")


def pseudonym(prefix: str, value: str) -> str:
    """결정적 가명 토큰. salt(.env, git 제외)를 섞어 역산 불가. 동일 원본→동일 토큰(조인 유지)."""
    h = hashlib.sha256(f"{settings.anon_hash_salt}|{prefix}|{value}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{h}"


def generalize_region(region: str) -> str:
    return region.split()[0]  # "서울특별시 송파구" -> "서울특별시"


def generalize_postal(postal: str) -> str:
    return postal[0] + "****"  # "032**" -> "0****" (region 정밀도와 일치)


# =====================================================================
# 로컬억제 (customers, k=3) — CSV 800건 드라이런으로 검증된 로직
# =====================================================================
def enforce_k(rows: list[dict], k: int = K):
    """rows: mart.customers dict 리스트. w_age/w_region/w_postal 작업필드를 채워 반환.
    반환: (rows, remaining_deficient). remaining 이 비어있지 않으면 k 미달 잔존(중단 신호)."""
    for r in rows:
        r["w_age"] = r["age_band"]
        r["w_region"] = r["resident_region"]
        r["w_postal"] = r["postal_code"]

    # 2단계: 미달 행만 region/postal 동반 일반화
    counts = Counter((r["gender"], r["w_age"], r["w_region"]) for r in rows)
    for r in rows:
        if counts[(r["gender"], r["w_age"], r["w_region"])] < k:
            r["w_region"] = generalize_region(r["w_region"])
            r["w_postal"] = generalize_postal(r["w_postal"])

    # 3단계: 반복 age 병합 (인접 기존 라벨로만, 새 라벨 금지)
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
            # 병합 후 그룹이 가장 커지는 이웃, 동점이면 더 젊은(낮은 index) 쪽 — 결정적
            target = max(neighbors, key=lambda nb: (counts.get((g, nb, region), 0), -AGE_BANDS.index(nb)))
            for r in rows:
                if r["gender"] == g and r["w_age"] == age and r["w_region"] == region:
                    r["w_age"] = target
            progressed = True
            break  # 병합 1회마다 재계산
        if not progressed:
            # age 병합으로 해소 불가 → gender 자동 처리하지 않고 중단(사람 판단)
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
# anon.transactions 는 device_id/terminal_id 대신 *_frequency_band
TX_INSERT_COLS = TX_COLS[:-2] + ["device_frequency_band", "terminal_frequency_band"]


def insert_many(cur, table: str, cols: list[str], rows: list[tuple]):
    placeholders = ", ".join(["%s"] * len(cols))
    cur.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})", rows)


def run(cur):
    # ---- mcc_codes: 그대로 복제 ----
    mcc = fetch_all(cur, "mart.mcc_codes", MCC_COLS)
    insert_many(cur, "anon.mcc_codes", MCC_COLS, [(m["mcc_code"], m["mcc_name"]) for m in mcc])

    # ---- customers: 로컬억제 ----
    cust = fetch_all(cur, "mart.customers", CUST_COLS)
    cust, remaining = enforce_k(cust)
    if remaining:
        raise SystemExit(f"[중단] k={K} 미달 그룹이 age 병합으로 해소되지 않음(성별 처리 필요): {remaining}")
    cust_rows = [(c["customer_id"], c["gender"], c["w_age"], c["w_region"], c["w_postal"],
                  c["occupation"], c["annual_income_band"], c["marital_status"]) for c in cust]
    insert_many(cur, "anon.customers", CUST_COLS, cust_rows)

    # ---- cards: 그대로 복제 ----
    cards = fetch_all(cur, "mart.cards", CARD_COLS)
    insert_many(cur, "anon.cards", CARD_COLS, [tuple(c[k] for k in CARD_COLS) for c in cards])

    # ---- merchants: name/BRN 가명화 ----
    merch = fetch_all(cur, "mart.merchants", MERCH_COLS)
    merch_rows = []
    for m in merch:
        row = dict(m)
        row["merchant_name"] = pseudonym("MERCH", m["merchant_name"])
        row["business_registration_number"] = pseudonym("BRN", m["business_registration_number"])
        merch_rows.append(tuple(row[k] for k in MERCH_COLS))
    insert_many(cur, "anon.merchants", MERCH_COLS, merch_rows)

    # ---- transactions: 금액/시각 라운딩 + device/terminal 버킷 ----
    tx = fetch_all(cur, "mart.transactions", TX_COLS)
    dev_freq = Counter(t["device_id"] for t in tx if t["device_id"] is not None)
    term_freq = Counter(t["terminal_id"] for t in tx if t["terminal_id"] is not None)
    tx_rows = []
    for t in tx:
        amount = round_sig(t["transaction_amount"])
        krw = recompute_krw(amount, t["applied_exchange_rate"])
        dt = trunc_hour(t["transaction_datetime"])
        dband = device_band(dev_freq[t["device_id"]]) if t["device_id"] is not None else None
        tband = terminal_band(term_freq[t["terminal_id"]])  # terminal_id 는 항상 존재
        tx_rows.append((
            t["transaction_id"], t["card_number_masked"], t["merchant_id"], t["mcc_code"], dt,
            t["approval_status"], t["decline_reason_code"], amount, t["currency_code"], krw,
            t["applied_exchange_rate"], t["merchant_country_code"], t["installment_months"],
            t["approval_channel"], t["pos_entry_mode"], t["auth_method"], t["ip_address"],
            dband, tband,
        ))
    insert_many(cur, "anon.transactions", TX_INSERT_COLS, tx_rows)

    return {"mcc": len(mcc), "customers": len(cust), "cards": len(cards),
            "merchants": len(merch), "transactions": len(tx)}


# =====================================================================
# 검증 (적재 후, 트리거 재활성 상태에서 세트 기반)
# =====================================================================
VALIDATIONS = [
    # (설명, 위반이면 >0 을 반환하는 쿼리)
    ("행수 1:1 mcc", "SELECT (SELECT count(*) FROM anon.mcc_codes)<>(SELECT count(*) FROM mart.mcc_codes)"),
    ("행수 1:1 customers", "SELECT (SELECT count(*) FROM anon.customers)<>(SELECT count(*) FROM mart.customers)"),
    ("행수 1:1 cards", "SELECT (SELECT count(*) FROM anon.cards)<>(SELECT count(*) FROM mart.cards)"),
    ("행수 1:1 merchants", "SELECT (SELECT count(*) FROM anon.merchants)<>(SELECT count(*) FROM mart.merchants)"),
    ("행수 1:1 transactions", "SELECT (SELECT count(*) FROM anon.transactions)<>(SELECT count(*) FROM mart.transactions)"),
    ("k>=3 위반 그룹",
     "SELECT count(*) FROM (SELECT 1 FROM anon.customers "
     "GROUP BY gender, age_band, resident_region HAVING count(*) < 3) q"),
    ("시각 분·초!=0", "SELECT count(*) FROM anon.transactions "
     "WHERE extract(minute FROM transaction_datetime)<>0 OR extract(second FROM transaction_datetime)<>0"),
    ("krw 파생오차>1", "SELECT count(*) FROM anon.transactions "
     "WHERE abs(krw_converted_amount - transaction_amount*applied_exchange_rate) > 1"),
    ("amount<=0", "SELECT count(*) FROM anon.transactions WHERE transaction_amount<=0 OR krw_converted_amount<=0"),
    ("ip/device 짝 위반", "SELECT count(*) FROM anon.transactions "
     "WHERE (ip_address IS NULL) <> (device_frequency_band IS NULL)"),
    ("BRN 가명화 안됨(mart와 동일)",
     "SELECT count(*) FROM anon.merchants a JOIN mart.merchants m USING (merchant_id) "
     "WHERE a.business_registration_number = m.business_registration_number"),
]


def validate(cur):
    print("\n=== 검증 ===")
    ok = True
    for desc, q in VALIDATIONS:
        cur.execute(q)
        v = cur.fetchone()[0]
        bad = bool(v)
        ok = ok and not bad
        print(f"  [{'FAIL' if bad else 'PASS'}] {desc}: {v}")
    return ok


# =====================================================================
# 메인 — 멱등 적재 (트리거 disable → truncate → insert → 검증 → enable)
# =====================================================================
def main():
    if not settings.anon_hash_salt:
        raise SystemExit("ANON_HASH_SALT 가 .env 에 설정돼 있어야 합니다 (merchant 가명화 salt).")

    dsn = settings.hanacard_migration_database_url.replace("+psycopg", "")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE anon.transactions DISABLE TRIGGER USER")
            cur.execute("TRUNCATE anon.transactions, anon.merchants, anon.cards, "
                        "anon.customers, anon.mcc_codes")
            counts = run(cur)
            cur.execute("ALTER TABLE anon.transactions ENABLE TRIGGER USER")
            ok = validate(cur)
        if ok:
            conn.commit()
            print(f"\n✅ 커밋 완료: {counts}")
        else:
            conn.rollback()
            raise SystemExit("\n❌ 검증 실패 → 롤백. anon 은 변경되지 않았습니다.")


if __name__ == "__main__":
    main()
