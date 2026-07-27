-- =====================================================================
-- [V005] anon 스키마 — 익명 데이터 계층
-- 대상: PostgreSQL 17, hanacard DB 안에 mart와 나란히 존재
--
-- 구성: 테이블 + CHECK 22종 + mcc 인덱스 + 미래날짜 CHECK 3종 +
--   mart(V004)와 동일한 정합성 트리거 5종.
--   "mart에 있는 안전망은 anon에도 전부 있다" 원칙 (2026-07 확정).
--
-- 전제: V002~V004(mart)가 적용돼 있고, agent_svc 계정이 존재할 것(README 참고).
-- 실행: psql -v ON_ERROR_STOP=1 -U hanacard_admin -d hanacard \
--             -f migrations/V005__anon_schema.sql
--   ※ 반드시 hanacard_admin으로 실행 — 소유자만 이후 TRUNCATE·트리거 제어가 가능하며,
--     익명화 배치(scripts/anon_batch/fill_anon.py)가 이 권한을 필요로 한다.
-- 다음: alembic upgrade head (service) → V006 (service GRANT)
--
-- 중요: 이 파일은 "그릇"만 만든다. 실제 값(k=3 일반화·로컬 억제 적용된 값)을
-- 채워넣는 배치 스크립트는 별도로 작성 예정.
-- anon 테이블은 대응하는 mart 테이블과 row 수·PK가 항상 1:1이어야 한다
-- (LLM이 "없는 데이터"를 찾아 헤매지 않도록).
--
-- FK는 전부 anon 스키마 내부끼리만 참조한다(= mart를 참조하지 않음).
-- 이렇게 하는 이유: agent_svc에게 anon 스키마만 GRANT해도 완결적으로 동작해야
-- 하기 때문 — mart를 참조하면 agent_svc가 mart 접근권한도 필요해져
-- "LLM은 mart에 직접 접근하지 않는다"는 원칙이 깨진다.
-- =====================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS anon;

COMMENT ON SCHEMA anon IS
'익명 데이터 계층. LLM/AI 에이전트가 조회하는 영역(agent_svc GRANT 대상).
mart를 원본으로 하되, 준식별자 조합의 재식별 위험을 통계적으로 낮춘 상태.

핵심 준식별자: 성별(gender) + 연령대(age_band) + 거주지역(resident_region).
목표 k=3 (2026-07 기준, 검증 데이터 800명 규모 대비 선정 — 안내서 실제 카드사
사례와 동일 값. 데이터 규모가 2만 명으로 커지면 재검토 가능하나, 규모가 커질수록
동일 k를 유지하는 데 드는 손실은 오히려 줄어드는 구조이므로 k=3을 상향해야만
하는 강제 사유는 아님).

postal_code는 resident_region과 사실상 동일한 지리정보(우편번호 앞 3자리가
자치구와 1:1 대응)이므로, resident_region이 일반화되는 행은 postal_code도
반드시 함께 일반화한다 — 하나만 뭉개면 다른 하나로 역산 가능하기 때문.

occupation/annual_income_band/marital_status는 핵심 준식별자 조합에 포함하지
않는다(포함 시 데이터 대부분이 삭제 수준으로 손상됨 — 800명 기준 실측 완료).
민감정보로만 취급하며 자체 일반화는 하지 않는다(2026-07 기준, 필요시 재검토).

transactions의 device_id/terminal_id는 "동일 기기·단말기 반복 사용"이라는
연결(linkage) 자체가 존재 목적이라 준식별자처럼 완충 단계로 일반화할 수 없음
(가명처리와 근본적으로 충돌하는 컬럼). 원본 토큰 대신 등장 빈도 구간으로
대체하여 부정거래탐지 신호는 보존하고 개별 기기 추적은 차단한다
(device_frequency_band, terminal_frequency_band).

transaction_datetime/transaction_amount/krw_converted_amount는 mart와 동일한
타입을 유지하되, 값의 정밀도는 익명화 배치가 축소해 채운다 — 이 스크립트는
구조만 만들고 값 자체는 다루지 않는다.';


-- ---------------------------------------------------------------------
-- 1. anon.mcc_codes — mart와 완전 동일 (개인정보 아님, 순수 참조 데이터)
-- ---------------------------------------------------------------------
CREATE TABLE anon.mcc_codes (
    mcc_code  INTEGER     NOT NULL,
    mcc_name  VARCHAR(50) NOT NULL,

    CONSTRAINT pk_anon_mcc_codes PRIMARY KEY (mcc_code)
);

COMMENT ON TABLE anon.mcc_codes IS
'mart.mcc_codes를 그대로 복제. 업종 코드는 개인정보가 아니므로 가공 없음.';


-- ---------------------------------------------------------------------
-- 2. anon.customers
-- ---------------------------------------------------------------------
CREATE TABLE anon.customers (
    customer_id          VARCHAR(32) NOT NULL,
    gender               VARCHAR(4)  NOT NULL,
    age_band             VARCHAR(10) NOT NULL,
    resident_region      VARCHAR(50) NOT NULL,
    postal_code          VARCHAR(10) NOT NULL,
    occupation           VARCHAR(30) NOT NULL,
    annual_income_band   VARCHAR(40) NOT NULL,
    marital_status       VARCHAR(10) NOT NULL,

    CONSTRAINT pk_anon_customers PRIMARY KEY (customer_id)
);

COMMENT ON TABLE anon.customers IS
'mart.customers 기반. row 수는 mart와 항상 동일(1:1) — 삭제 대신 로컬 억제로
k-익명성을 만족시키므로 인원이 줄지 않음.';

COMMENT ON COLUMN anon.customers.gender IS
'핵심 준식별자 1/3. 값은 mart와 동일한 도메인(M/F)이나, k 미달 조합은
로컬 억제 배치가 채워넣을 때 이 컬럼도 대상이 될 수 있음(현재 설계상 최후 수단).';

COMMENT ON COLUMN anon.customers.age_band IS
'핵심 준식별자 2/3. k 미달 시 인접 구간으로 병합될 수 있음(예: "70대 이상"→"60대").
mart처럼 고정 7개 구간이 아니라, anon에서는 실제 존재하는 값의 종류가 배치 결과에
따라 mart보다 적을 수 있음(병합으로 인해).';

COMMENT ON COLUMN anon.customers.resident_region IS
'핵심 준식별자 3/3. k 미달 시 자치구 단위 대신 "서울특별시"로 일반화될 수 있음.
postal_code와 반드시 같은 단계에서 함께 처리됨(주석 스키마 설명 참고).';

COMMENT ON COLUMN anon.customers.postal_code IS
'resident_region과 동반 일반화 대상. resident_region이 뭉개진 행은 이 컬럼도
반드시 뭉개져야 함(예: 앞 3자리 → 앞 2자리, 또는 NULL) — 그렇지 않으면
resident_region 일반화가 무의미해짐(우편번호로 역산 가능).';

COMMENT ON COLUMN anon.customers.occupation IS
'민감정보로 취급, 핵심 준식별자 조합에 미포함. mart 값 그대로 유지(2026-07 기준).';

COMMENT ON COLUMN anon.customers.annual_income_band IS
'민감정보로 취급, 핵심 준식별자 조합에 미포함. mart 값 그대로 유지(2026-07 기준).';

COMMENT ON COLUMN anon.customers.marital_status IS
'민감정보로 취급, 핵심 준식별자 조합에 미포함. mart 값 그대로 유지(2026-07 기준).';


-- ---------------------------------------------------------------------
-- 3. anon.cards — mart와 동일 구조 (핵심 준식별자 조합 대상 아님)
-- ---------------------------------------------------------------------
CREATE TABLE anon.cards (
    card_number_masked         VARCHAR(32) NOT NULL,
    customer_id                VARCHAR(32) NOT NULL,
    card_product_code          VARCHAR(20) NOT NULL,
    card_issue_month           VARCHAR(7)  NOT NULL,
    credit_limit_band          VARCHAR(30) NOT NULL,
    card_status                VARCHAR(10) NOT NULL,
    card_status_changed_month  VARCHAR(7),
    signup_channel              VARCHAR(20) NOT NULL,

    CONSTRAINT pk_anon_cards PRIMARY KEY (card_number_masked),
    CONSTRAINT fk_anon_cards_customer FOREIGN KEY (customer_id)
        REFERENCES anon.customers (customer_id)
);

COMMENT ON TABLE anon.cards IS
'mart.cards와 동일 구조. FK는 anon.customers만 참조(mart 미참조).
카드 속성 자체는 현재 핵심 준식별자 조합 대상이 아니므로 mart 값 그대로 유지.';


-- ---------------------------------------------------------------------
-- 4. anon.merchants — mart와 동일 구조
-- ---------------------------------------------------------------------
CREATE TABLE anon.merchants (
    merchant_id                     VARCHAR(32)  NOT NULL,
    merchant_name                   VARCHAR(100) NOT NULL,
    business_registration_number    VARCHAR(32)  NOT NULL,
    mcc_code                        INTEGER      NOT NULL,
    franchise_hq_code               VARCHAR(20),
    merchant_region                 VARCHAR(50)  NOT NULL,
    merchant_open_month             VARCHAR(7)   NOT NULL,
    fee_tier_code                   VARCHAR(10)  NOT NULL,
    merchant_status                 VARCHAR(10)  NOT NULL,
    merchant_status_changed_month   VARCHAR(7),

    CONSTRAINT pk_anon_merchants PRIMARY KEY (merchant_id),
    CONSTRAINT fk_anon_merchants_mcc FOREIGN KEY (mcc_code)
        REFERENCES anon.mcc_codes (mcc_code)
);

COMMENT ON TABLE anon.merchants IS
'mart.merchants와 동일 구조. 가맹점측 익명화 처리 방침(예: 개인사업자
merchant_name과 business_registration_number는 익명화 배치가 결정적 가명 토큰으로
치환해 채운다(원본 대비 역산 불가). merchant_id는 조인 유지를 위해 mart와 동일하다.';


-- ---------------------------------------------------------------------
-- 5. anon.transactions
-- ---------------------------------------------------------------------
CREATE TABLE anon.transactions (
    transaction_id          VARCHAR(20)   NOT NULL,
    card_number_masked      VARCHAR(32)   NOT NULL,
    merchant_id              VARCHAR(32),
    mcc_code                 INTEGER       NOT NULL,
    transaction_datetime     TIMESTAMPTZ   NOT NULL,
    approval_status          VARCHAR(10)   NOT NULL,
    decline_reason_code      VARCHAR(20),
    transaction_amount       NUMERIC(15,2) NOT NULL,
    currency_code            VARCHAR(3)    NOT NULL,
    krw_converted_amount     NUMERIC(15,2) NOT NULL,
    applied_exchange_rate    NUMERIC(12,4) NOT NULL,
    merchant_country_code    VARCHAR(2)    NOT NULL,
    installment_months       SMALLINT      NOT NULL,
    approval_channel         VARCHAR(20)   NOT NULL,
    pos_entry_mode           VARCHAR(20)   NOT NULL,
    auth_method               VARCHAR(20)   NOT NULL,
    ip_address                VARCHAR(20),

    -- device_id/terminal_id 대체 — 원본 토큰 대신 등장 빈도 구간
    device_frequency_band     VARCHAR(20),
    terminal_frequency_band   VARCHAR(20) NOT NULL,

    CONSTRAINT pk_anon_transactions PRIMARY KEY (transaction_id),
    CONSTRAINT fk_anon_tx_card FOREIGN KEY (card_number_masked)
        REFERENCES anon.cards (card_number_masked),
    CONSTRAINT fk_anon_tx_merchant FOREIGN KEY (merchant_id)
        REFERENCES anon.merchants (merchant_id),
    CONSTRAINT fk_anon_tx_mcc FOREIGN KEY (mcc_code)
        REFERENCES anon.mcc_codes (mcc_code),

    -- ip_address와 device_frequency_band는 온라인 거래에만 존재 (mart의 짝 제약과 동일 원리)
    CONSTRAINT ck_anon_tx_ip_device_pair CHECK (
        (ip_address IS NULL) = (device_frequency_band IS NULL)
    )
);

COMMENT ON TABLE anon.transactions IS
'mart.transactions 기반. row 수는 mart와 항상 동일(1:1).
transaction_datetime/transaction_amount/krw_converted_amount는 mart와 동일 타입
유지 — 값의 정밀도는 익명화 배치가 축소해 채운다.';

COMMENT ON COLUMN anon.transactions.device_frequency_band IS
'device_id 대체. 동일 기기가 이 데이터셋에서 몇 번 등장했는지를 구간화한 값
(예: "1회", "2~5회", "6회 이상"). 실제 기기를 특정할 수는 없으나
"이 기기가 여러 카드에서 반복 사용됐다" 같은 부정거래 패턴 신호는 보존.
NULL = 오프라인 거래(ip_address와 동일한 NULL 패턴).';

COMMENT ON COLUMN anon.transactions.terminal_frequency_band IS
'terminal_id 대체. 동일 단말기가 이 데이터셋에서 몇 번 등장했는지를 구간화한 값.
mart의 terminal_id는 NOT NULL이므로 이 컬럼도 NOT NULL 유지.
"한 단말기에 여러 카드가 몰린다" 같은 카드 복제 의심 신호를 실제 단말기 식별 없이 보존하는 목적.';

COMMENT ON COLUMN anon.transactions.ip_address IS
'mart와 동일하게 /24 대역 단위 유지. 추가 일반화(예: /16) 필요 여부는
배치 로직에서 그룹 크기 실측 후 결정 (이번 스크립트 범위 아님).';

-- 인덱스: mart와 동일한 최소 구성
CREATE INDEX idx_anon_tx_card     ON anon.transactions (card_number_masked);
CREATE INDEX idx_anon_tx_merchant ON anon.transactions (merchant_id);
CREATE INDEX idx_anon_tx_datetime ON anon.transactions (transaction_datetime);




-- ---------------------------------------------------------------------
-- customers
-- ---------------------------------------------------------------------
ALTER TABLE anon.customers
    ADD CONSTRAINT ck_anon_customers_gender CHECK (gender IN ('M', 'F'));

-- mart는 '^\d{3}\*\*$' 고정이나, 로컬 억제로 자리수가 더 뭉개지거나
-- NULL 처리될 수 있어 anon은 느슨하게: 최소 1자리 숫자 + 별표 1개 이상, 또는 NULL
ALTER TABLE anon.customers
    ADD CONSTRAINT ck_anon_customers_postal CHECK (
        postal_code IS NULL OR postal_code ~ '^\d{1,3}\*+$'
    );

-- ---------------------------------------------------------------------
-- cards — 익명화 대상 아님, mart와 동일 제약
-- ---------------------------------------------------------------------
ALTER TABLE anon.cards
    ADD CONSTRAINT ck_anon_cards_issue_month CHECK (card_issue_month ~ '^\d{4}-\d{2}$'),
    ADD CONSTRAINT ck_anon_cards_status CHECK (card_status IN ('정상', '일시정지', '해지')),
    ADD CONSTRAINT ck_anon_cards_status_changed CHECK (
        (card_status = '정상' AND card_status_changed_month IS NULL)
     OR (card_status <> '정상' AND card_status_changed_month ~ '^\d{4}-\d{2}$')
    ),
    ADD CONSTRAINT ck_anon_cards_changed_after_issue CHECK (
        card_status_changed_month IS NULL
     OR card_status_changed_month >= card_issue_month
    );

-- ---------------------------------------------------------------------
-- merchants — 익명화 대상 아님, mart와 동일 제약
-- ---------------------------------------------------------------------
ALTER TABLE anon.merchants
    ADD CONSTRAINT ck_anon_merchants_open_month CHECK (merchant_open_month ~ '^\d{4}-\d{2}$'),
    ADD CONSTRAINT ck_anon_merchants_fee_tier CHECK (fee_tier_code IN ('영세', '중소1', '중소2', '일반')),
    ADD CONSTRAINT ck_anon_merchants_status CHECK (merchant_status IN ('정상', '휴업', '폐업')),
    ADD CONSTRAINT ck_anon_merchants_status_changed CHECK (
        (merchant_status = '정상' AND merchant_status_changed_month IS NULL)
     OR (merchant_status <> '정상' AND merchant_status_changed_month ~ '^\d{4}-\d{2}$')
    ),
    ADD CONSTRAINT ck_anon_merchants_changed_after_open CHECK (
        merchant_status_changed_month IS NULL
     OR merchant_status_changed_month >= merchant_open_month
    );

-- ---------------------------------------------------------------------
-- transactions — device_id/terminal_id 관련 제약만 이미 V1에서 처리됨(ck_anon_tx_ip_device_pair).
-- 나머지는 익명화 대상이 아닌 필드들이므로 mart와 동일하게 보강.
-- ---------------------------------------------------------------------
ALTER TABLE anon.transactions
    ADD CONSTRAINT ck_anon_tx_amount_positive CHECK (
        transaction_amount > 0 AND krw_converted_amount > 0
    ),
    ADD CONSTRAINT ck_anon_tx_krw_derived CHECK (
        abs(krw_converted_amount - transaction_amount * applied_exchange_rate) <= 1
    ),
    ADD CONSTRAINT ck_anon_tx_decline_consistent CHECK (
        (approval_status = '거절' AND decline_reason_code IS NOT NULL)
     OR (approval_status = '승인' AND decline_reason_code IS NULL)
    ),
    ADD CONSTRAINT ck_anon_tx_channel_pos_match CHECK (
        (pos_entry_mode IN ('IC칩', 'NFC', '마그네틱') AND approval_channel = '오프라인')
     OR (pos_entry_mode = 'CNP(온라인입력)'            AND approval_channel = '온라인_PG')
     OR (pos_entry_mode = 'APP_QR'                     AND approval_channel = '앱카드')
    ),
    ADD CONSTRAINT ck_anon_tx_auth_pos_match CHECK (
        (pos_entry_mode IN ('IC칩', 'NFC', '마그네틱') AND auth_method IN ('PIN', '서명', '없음'))
     OR (pos_entry_mode = 'CNP(온라인입력)'            AND auth_method IN ('3D Secure', '없음'))
     OR (pos_entry_mode = 'APP_QR'                     AND auth_method IN ('생체인증', 'PIN'))
    ),
    ADD CONSTRAINT ck_anon_tx_offline_no_ip CHECK (
        approval_channel <> '오프라인' OR ip_address IS NULL
    ),
    ADD CONSTRAINT ck_anon_tx_installment CHECK (installment_months IN (0, 2, 3, 6, 12)),
    ADD CONSTRAINT ck_anon_tx_status CHECK (approval_status IN ('승인', '거절')),
    ADD CONSTRAINT ck_anon_tx_rate_positive CHECK (applied_exchange_rate > 0),
    ADD CONSTRAINT ck_anon_tx_domestic CHECK (
        merchant_id IS NULL
     OR (merchant_country_code = 'KR' AND currency_code = 'KRW')
    );

-- ---------------------------------------------------------------------
-- 인덱스 — mcc_code 필터/집계가 핵심 분석 축인데 인덱스 누락 확인(EXPLAIN 실측 완료)
-- ---------------------------------------------------------------------
CREATE INDEX idx_anon_tx_mcc ON anon.transactions (mcc_code);

-- ---------------------------------------------------------------------
-- 미래날짜 CHECK — mart와 동일 (배치 스크립트 날짜 버그 안전망)
-- ---------------------------------------------------------------------
ALTER TABLE anon.cards
    ADD CONSTRAINT ck_anon_cards_issue_not_future CHECK (
        card_issue_month <= to_char(now(), 'YYYY-MM')
    );

ALTER TABLE anon.merchants
    ADD CONSTRAINT ck_anon_merchants_open_not_future CHECK (
        merchant_open_month <= to_char(now(), 'YYYY-MM')
    );

ALTER TABLE anon.transactions
    ADD CONSTRAINT ck_anon_tx_not_future CHECK (
        transaction_datetime <= now()
    );

-- ---------------------------------------------------------------------
-- 정합성 트리거 5종 — mart V3와 동일 로직의 anon 버전
--
-- 필요한 이유: anon은 익명화 배치 스크립트가 채우는데, CHECK는 "같은 행 안"
-- 규칙만 잡는다. 배치가 조인을 잘못 걸어 카드-거래 짝이 어긋난 채 들어가는
-- 유형의 버그는 다른 테이블 참조가 필요해 트리거만 잡을 수 있다.
-- ("mart에 있는 안전망은 anon에도 전부 있다" 원칙)
--
-- 대량 적재 시: mart와 동일하게 DISABLE TRIGGER → 적재 → 검증 → ENABLE 절차 사용.
-- ---------------------------------------------------------------------

-- [1] 거래일시 >= 카드 발급월
CREATE OR REPLACE FUNCTION anon.fn_tx_after_card_issue()
RETURNS trigger AS $$
DECLARE
    v_issue VARCHAR(7);
BEGIN
    SELECT card_issue_month INTO v_issue
    FROM anon.cards WHERE card_number_masked = NEW.card_number_masked;

    IF v_issue IS NOT NULL
       AND to_char(NEW.transaction_datetime, 'YYYY-MM') < v_issue THEN
        RAISE EXCEPTION
            '[anon 정합성 위반] 카드 발급 전 거래: transaction_id=%, 거래=%, 발급월=%',
            NEW.transaction_id, to_char(NEW.transaction_datetime, 'YYYY-MM'), v_issue
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_anon_tx_after_card_issue
    BEFORE INSERT OR UPDATE ON anon.transactions
    FOR EACH ROW EXECUTE FUNCTION anon.fn_tx_after_card_issue();

-- [2] 해지된 카드는 해지월 이후 거래 불가
CREATE OR REPLACE FUNCTION anon.fn_tx_before_card_terminated()
RETURNS trigger AS $$
DECLARE
    v_status  VARCHAR(10);
    v_changed VARCHAR(7);
BEGIN
    SELECT card_status, card_status_changed_month INTO v_status, v_changed
    FROM anon.cards WHERE card_number_masked = NEW.card_number_masked;

    IF v_status = '해지' AND v_changed IS NOT NULL
       AND to_char(NEW.transaction_datetime, 'YYYY-MM') > v_changed THEN
        RAISE EXCEPTION
            '[anon 정합성 위반] 해지 카드의 해지 후 거래: transaction_id=%, 거래=%, 해지월=%',
            NEW.transaction_id, to_char(NEW.transaction_datetime, 'YYYY-MM'), v_changed
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_anon_tx_before_card_terminated
    BEFORE INSERT OR UPDATE ON anon.transactions
    FOR EACH ROW EXECUTE FUNCTION anon.fn_tx_before_card_terminated();

-- [3] 거래일시가 가맹점 영업 기간 내 (해외거래는 검사 제외)
CREATE OR REPLACE FUNCTION anon.fn_tx_merchant_active()
RETURNS trigger AS $$
DECLARE
    v_open    VARCHAR(7);
    v_status  VARCHAR(10);
    v_changed VARCHAR(7);
    v_month   VARCHAR(7) := to_char(NEW.transaction_datetime, 'YYYY-MM');
BEGIN
    IF NEW.merchant_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT merchant_open_month, merchant_status, merchant_status_changed_month
      INTO v_open, v_status, v_changed
    FROM anon.merchants WHERE merchant_id = NEW.merchant_id;

    IF v_open IS NOT NULL AND v_month < v_open THEN
        RAISE EXCEPTION
            '[anon 정합성 위반] 가맹점 개업 전 거래: transaction_id=%, 거래=%, 개업월=%',
            NEW.transaction_id, v_month, v_open
            USING ERRCODE = 'check_violation';
    END IF;

    IF v_status = '폐업' AND v_changed IS NOT NULL AND v_month > v_changed THEN
        RAISE EXCEPTION
            '[anon 정합성 위반] 폐업 가맹점의 폐업 후 거래: transaction_id=%, 거래=%, 폐업월=%',
            NEW.transaction_id, v_month, v_changed
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_anon_tx_merchant_active
    BEFORE INSERT OR UPDATE ON anon.transactions
    FOR EACH ROW EXECUTE FUNCTION anon.fn_tx_merchant_active();

-- [4] 국내거래 MCC 일치 (해외거래는 검사 제외)
CREATE OR REPLACE FUNCTION anon.fn_tx_mcc_match()
RETURNS trigger AS $$
DECLARE
    v_mcc INTEGER;
BEGIN
    IF NEW.merchant_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT mcc_code INTO v_mcc
    FROM anon.merchants WHERE merchant_id = NEW.merchant_id;

    IF v_mcc IS NOT NULL AND v_mcc <> NEW.mcc_code THEN
        RAISE EXCEPTION
            '[anon 정합성 위반] 국내거래 MCC 불일치: transaction_id=%, 거래MCC=%, 가맹점MCC=%',
            NEW.transaction_id, NEW.mcc_code, v_mcc
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_anon_tx_mcc_match
    BEFORE INSERT OR UPDATE ON anon.transactions
    FOR EACH ROW EXECUTE FUNCTION anon.fn_tx_mcc_match();

-- [5] 체크카드 할부 금지
CREATE OR REPLACE FUNCTION anon.fn_tx_check_card_no_installment()
RETURNS trigger AS $$
DECLARE
    v_product VARCHAR(20);
BEGIN
    IF NEW.installment_months = 0 THEN
        RETURN NEW;
    END IF;

    SELECT card_product_code INTO v_product
    FROM anon.cards WHERE card_number_masked = NEW.card_number_masked;

    IF v_product LIKE 'CK%' THEN
        RAISE EXCEPTION
            '[anon 정합성 위반] 체크카드 할부 거래: transaction_id=%, 상품=%, 할부=%개월',
            NEW.transaction_id, v_product, NEW.installment_months
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_anon_tx_check_card_no_installment
    BEFORE INSERT OR UPDATE ON anon.transactions
    FOR EACH ROW EXECUTE FUNCTION anon.fn_tx_check_card_no_installment();

COMMIT;

-- ============================================================
-- 권한 부여 (GRANT) — agent_svc는 anon만, 읽기 전용
-- ============================================================
GRANT USAGE ON SCHEMA anon TO agent_svc;
GRANT SELECT ON ALL TABLES IN SCHEMA anon TO agent_svc;
ALTER DEFAULT PRIVILEGES IN SCHEMA anon
    GRANT SELECT ON TABLES TO agent_svc;

REVOKE ALL ON SCHEMA anon FROM PUBLIC;

-- ============================================================
-- 이 스키마를 채우는 주체 (2026-07-27 기준)
-- ============================================================
-- 이 파일은 "그릇"만 만든다. 값은 익명화 배치가 mart를 읽어 채운다:
--     python scripts/anon_batch/fill_anon.py   (레포)
--
-- 배치가 수행하는 처리(요약 — 구체 파라미터는 코드와 코드 주석에 둔다):
--   customers   : 준식별자 조합의 k-익명성 확보를 위한 로컬 억제.
--                 resident_region과 postal_code는 항상 같은 단계에서 함께 일반화.
--   transactions: 금액·시각의 정밀도 축소, device_id/terminal_id를 빈도 구간으로 대체,
--                 ip_address는 mart와 동일한 대역 단위 유지.
--   merchants   : 상호명·사업자등록번호를 결정적 가명 토큰으로 치환
--                 (salt는 .env로만 주입하며 저장소에 두지 않는다).
--   cards/mcc_codes : 준식별자 대상이 아니므로 mart 값을 그대로 복제.
--
-- 배치는 재실행 가능(멱등)하며, 대량 적재를 위해 트리거를 일시 비활성화한 뒤
-- 적재·검증·재활성화한다. 그래서 이 스키마의 소유자 계정으로 실행해야 한다.
