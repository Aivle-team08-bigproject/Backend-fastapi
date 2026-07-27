-- =====================================================================
-- [V002] mart 스키마 — 테이블 + PK/UNIQUE + CHECK + COMMENT
-- 대상: PostgreSQL 17
--
-- 실행: psql -v ON_ERROR_STOP=1 -U portfolio_admin -d portfolio \
--             -f migrations/V002__mart_tables.sql
--   ※ 반드시 portfolio_admin으로 실행할 것 — PostgreSQL은 CREATE TABLE을 실행한
--     계정을 소유자로 삼으며, 소유자만 이후 ALTER/TRUNCATE/트리거 제어가 가능하다.
-- 이전: V001 (역할·스키마·기본권한)   다음: V003 (FK) → V004 (트리거)
--
-- 구조: 원본 CSV 3개를 정규화하여 5테이블로 분리(mcc_codes/customers/cards/
--       merchants/transactions). 컬럼 간 의존관계를 반영해 데이터를 재생성했다.
--
-- COMMENT 규칙: 의미 → 값 목록/형식 → NULL 의미 → 함정 → 가명처리 결과
--   원칙 1) 쿼리로 알 수 있는 것(건수·분포)은 적지 않는다.
--   원칙 2) 가명처리 "방법"은 적지 않고 "결과"만 적는다.
--           (해시 알고리즘·마스킹 위치·상한값 등은 재식별 공격의 단서가 되며,
--            분리 보관 원칙에 따라 결과 데이터와 함께 두지 않는다)
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS mart;

COMMENT ON SCHEMA mart IS
'가명 데이터 계층. 사람(담당자·분석가)이 조회하는 영역.
식별자는 가명 토큰으로 대체되고 연속값은 구간화되어 있으나, 여전히 가명정보이므로
개인정보보호법상 보호조치 대상. LLM/AI 에이전트는 이 스키마에 접근 권한이 없음
(GRANT 대상 아님, agent_svc는 anon 스키마만 사용).

데이터 범위: 서울 15개 자치구 한정.

상세 설계 근거(k-익명성 목표치, 준식별자 정의 등)는 각 테이블 코멘트 참고 —
스키마 레벨 COMMENT는 GRANT와 무관하게 누구나 조회 가능하므로(2026-07 실측 확인)
민감한 상세는 테이블/컬럼 코멘트에만 기재한다.';

-- ---------------------------------------------------------------------
-- 1. mart.mcc_codes — 업종 코드 마스터
-- ---------------------------------------------------------------------
CREATE TABLE mart.mcc_codes (
    mcc_code  INTEGER     NOT NULL,
    mcc_name  VARCHAR(50) NOT NULL,

    CONSTRAINT pk_mcc_codes PRIMARY KEY (mcc_code),
    CONSTRAINT uq_mcc_name UNIQUE (mcc_name)
);

COMMENT ON TABLE mart.mcc_codes IS
'업종 코드 마스터 (Merchant Category Code, ISO 18245 국제 표준).
merchants와 transactions 양쪽에서 참조한다.

분리 사유:
  1) 코드명 중복 제거 — 기존에는 mcc_name이 가맹점 행마다 반복 저장되어 3정규형 위반.
  2) 해외 전용 업종의 정의 — 국내 가맹점이 없는 업종(해외 교통/승차공유 등)은
     가맹점 마스터에 나타나지 않아 코드명을 조회할 방법이 없었음.
     이 테이블이 국내외 업종을 모두 정의하므로 해외 거래도 업종명을 얻을 수 있다.';

COMMENT ON COLUMN mart.mcc_codes.mcc_code IS
'업종 코드. PK. ISO 18245 표준 코드값.';

COMMENT ON COLUMN mart.mcc_codes.mcc_name IS
'업종명. UNIQUE — 코드와 1:1 대응.';

-- ---------------------------------------------------------------------
-- 2. mart.customers — 고객정보 (사람 속성만)
-- ---------------------------------------------------------------------
CREATE TABLE mart.customers (
    customer_id         VARCHAR(32) NOT NULL,
    gender              VARCHAR(4)  NOT NULL,
    age_band            VARCHAR(10) NOT NULL,
    resident_region     VARCHAR(50) NOT NULL,
    postal_code         VARCHAR(10) NOT NULL,
    occupation          VARCHAR(30) NOT NULL,
    annual_income_band  VARCHAR(40) NOT NULL,
    marital_status      VARCHAR(10) NOT NULL,

    CONSTRAINT pk_customers PRIMARY KEY (customer_id),
    CONSTRAINT ck_customers_gender CHECK (gender IN ('M', 'F')),
    CONSTRAINT ck_customers_postal CHECK (postal_code ~ '^\d{3}\*\*$')
);

COMMENT ON TABLE mart.customers IS
'카드 고객 마스터. 사람의 속성만 보유하며 카드 속성은 cards 테이블로 분리했다.

분리 사유: 고객과 카드는 별개의 엔티티이며 실제 카드사 환경에서는 1:N 관계.
  두 엔티티를 한 테이블에 병합하면 고객이 카드를 2장 이상 보유하는 순간
  customer_id가 중복되어 PK가 성립하지 않는다.

k-익명성: 준식별자(성별+연령대+거주지역) 조합에 1명뿐인 경우가 존재하여 현재 k=1.
  가명정보는 접근통제·분리보관을 전제로 하므로 내부 이용 목적상 허용 범위이나,
  외부 제공용 익명정보를 생성할 때는 k=3을 확보해야 함(anon 계층에서 처리).
  근거: 금융분야 가명익명처리 안내서의 실제 카드사 사례가 k=3을 사용했고,
  본 데이터(800명) 실측 결과 k=3은 손실 12.8%, k=5는 손실 38.1%로 과도함.
  데이터 규모가 2만 명 수준으로 커지면 동일 손실률로 더 큰 k도 확보 가능하므로
  그 시점에 k=5 상향을 재검토할 수 있음(상향이 강제되는 것은 아님).
  근본 원인은 고객 800명 대비 준식별자 조합 수(약 195개)가 많은 것으로,
  데이터 규모가 커지면 자연히 해소됨.';

COMMENT ON COLUMN mart.customers.customer_id IS
'고객 가명 ID. PK. 형식 CUST_+12자리 HEX.
원본 고객번호와 1:1 대응하나 역매핑 불가. cards.customer_id가 참조.';

COMMENT ON COLUMN mart.customers.gender IS
'성별. 값: M, F. CHECK 제약으로 강제.';

COMMENT ON COLUMN mart.customers.age_band IS
'연령대. 값: 10대, 20대, 30대, 40대, 50대, 60대, 70대 이상.
10세 단위 범주값만 존재 — 정확한 나이·생년월일은 조회 불가(설계상 의도).
occupation과 논리적으로 정합함(예: 10대는 학생·서비스직·프리랜서만 존재).';

COMMENT ON COLUMN mart.customers.resident_region IS
'거주 지역. "서울특별시 " + 자치구 형식. 서울 15개구만 존재
(강남/강서/관악/광진/구로/노원/동작/마포/서초/성동/송파/영등포/용산/은평/종로).
시군구 단위까지만 존재 — 동 단위 이하 및 좌표 분석 불가(설계상 의도).';

COMMENT ON COLUMN mart.customers.postal_code IS
'우편번호. 앞 3자리 + "**" 형식 (예: 061**). CHECK 제약으로 형식 강제.
실제 우편번호 체계를 따르므로 resident_region과 항상 정합함
(예: 강남구 → 060~064, 성동구 → 047~048).
주의 — resident_region과 중복 정보가 아님. 한 자치구에 여러 우편번호 대역이 존재하므로
우편번호가 지역보다 세밀하다. 다만 역방향(우편번호 → 지역)은 1:1로 결정됨.';

COMMENT ON COLUMN mart.customers.occupation IS
'직업. 값: 사무직, 자영업, 전문직, 서비스직, 프리랜서, 공무원, 학생, 무직/은퇴.
age_band에 따라 가능한 값이 제한됨 — 연령대별 직업 분포를 반영해 생성.';

COMMENT ON COLUMN mart.customers.annual_income_band IS
'연소득 구간. 1천만원 단위, "N~M원" 형식 (예: 30,000,000~39,999,999원).
구간값만 존재 — 정확한 소득액은 조회 불가(설계상 의도).
occupation·age_band와 정합함(예: 학생은 하위 구간, 40~50대 전문직은 상위 구간).
주의 — 문자열이므로 ORDER BY 시 사전순으로 정렬됨(1억원대가 2천만원대보다 앞에 옴).
숫자 크기순 정렬이 필요하면 별도 파싱 필요.';

COMMENT ON COLUMN mart.customers.marital_status IS
'결혼 여부. 값: 기혼, 미혼. 10~20대는 미혼으로 고정.';

-- 인덱스: 800행 테이블이므로 생성하지 않음.
-- (전체 스캔이 인덱스 스캔보다 빠르며 옵티마이저도 인덱스를 선택하지 않음.
--  인덱스는 언제든 무중단 추가 가능하므로 실측 후 필요 시 추가할 것)

-- ---------------------------------------------------------------------
-- 3. mart.cards — 카드정보
-- ---------------------------------------------------------------------
CREATE TABLE mart.cards (
    card_number_masked         VARCHAR(32) NOT NULL,
    customer_id                VARCHAR(32) NOT NULL,
    card_product_code          VARCHAR(20) NOT NULL,
    card_issue_month           VARCHAR(7)  NOT NULL,
    credit_limit_band          VARCHAR(30) NOT NULL,
    card_status                VARCHAR(10) NOT NULL,
    card_status_changed_month  VARCHAR(7),
    signup_channel             VARCHAR(20) NOT NULL,

    CONSTRAINT pk_cards PRIMARY KEY (card_number_masked),
    CONSTRAINT ck_cards_issue_month CHECK (card_issue_month ~ '^\d{4}-\d{2}$'),
    CONSTRAINT ck_cards_status CHECK (card_status IN ('정상', '일시정지', '해지')),

    -- 상태가 정상이면 변경월이 없고, 정상이 아니면 반드시 있어야 함
    CONSTRAINT ck_cards_status_changed CHECK (
        (card_status = '정상' AND card_status_changed_month IS NULL)
     OR (card_status <> '정상' AND card_status_changed_month ~ '^\d{4}-\d{2}$')
    ),

    -- 상태 변경은 발급 이후에만 발생할 수 있음
    CONSTRAINT ck_cards_changed_after_issue CHECK (
        card_status_changed_month IS NULL
     OR card_status_changed_month >= card_issue_month
    ),

    -- 발급월이 미래일 수 없음 (배치/생성 스크립트의 날짜 계산 버그 안전망.
    -- 2026-07 실측: 이 제약 없이는 2099-12 발급 카드도 적재됨)
    CONSTRAINT ck_cards_issue_not_future CHECK (
        card_issue_month <= to_char(now(), 'YYYY-MM')
    )
);

COMMENT ON TABLE mart.cards IS
'카드 마스터. customers에서 분리한 카드 속성을 보유한다.
고객:카드 = 1:N 구조 — 현재 데이터는 1:1이나 구조적으로 1:N을 수용한다.
transactions는 이 테이블을 통해 고객에 도달한다(transactions에 customer_id 없음).';

COMMENT ON COLUMN mart.cards.card_number_masked IS
'카드 가명 토큰. PK. 형식 CARD_+12자리 HEX.
원본 카드번호와 1:1 대응하나 역매핑 불가. transactions.card_number_masked가 참조.';

COMMENT ON COLUMN mart.cards.customer_id IS
'카드 소유 고객. customers.customer_id 참조 (V2에서 FK로 강제).
transactions에서 고객 정보가 필요하면 이 테이블을 경유해 조인한다.
주의 — transactions에 customer_id를 중복 저장하지 않는 이유: 두 경로(카드→고객,
직접 저장)가 어긋날 수 있고 FK 두 개로는 그 불일치를 막을 수 없기 때문.';

COMMENT ON COLUMN mart.cards.card_product_code IS
'카드 상품 코드. 값: CR-PT001, CR-MI002, CR-CB003(신용), CK-MB101, CK-YT102(체크).
접두 CR=신용카드, CK=체크카드. V3 트리거가 체크카드의 할부 거래를 차단함
(체크카드는 즉시 출금되므로 할부가 성립하지 않음).';

COMMENT ON COLUMN mart.cards.card_issue_month IS
'카드 발급 월. YYYY-MM 형식. CHECK 제약으로 형식 강제.
월 단위까지만 존재 — 정확한 발급일은 조회 불가(설계상 의도).
V3 트리거가 이 값보다 이른 거래를 차단함.';

COMMENT ON COLUMN mart.cards.credit_limit_band IS
'카드 승인한도 구간. 값: 500만원 미만, 500~1,000만원, 1,000~2,000만원,
2,000~3,000만원, 3,000만원 이상.
구간값만 존재 — 정확한 한도액은 조회 불가(설계상 의도).
고객의 소득 구간에 따라 상한이 결정됨 — 여신 심사 기준을 반영.
체크카드는 신용공여가 없으므로 낮은 구간만 부여됨.
주의 — 문자열이므로 크기순 정렬 시 별도 파싱 필요.';

COMMENT ON COLUMN mart.cards.card_status IS
'카드 상태. 값: 정상, 일시정지, 해지. CHECK 제약으로 강제.
조회 시점의 현재 상태이며, 변경 시점은 card_status_changed_month 참조.';

COMMENT ON COLUMN mart.cards.card_status_changed_month IS
'카드 상태가 마지막으로 변경된 월. YYYY-MM 형식.
NULL = 상태가 정상이며 변경된 적이 없음 — 결측이 아니라 해당 없음.

주의 — 최종 변경 시점만 기록하며 상태 변경 "이력"이 아님. 따라서 특정 시점의 카드
상태를 정확히 판정할 수는 없다.
  해지: 되돌릴 수 없으므로 이 월 이후 거래는 존재할 수 없음 → V3 트리거가 차단.
  일시정지: 해제 후 재사용이 가능하므로 이 월 이후 거래가 정상적으로 존재할 수 있음
            (정지 → 해제 → 사용 → 재정지). 따라서 차단하지 않음.';

COMMENT ON COLUMN mart.cards.signup_channel IS
'가입 경로. 값: 제휴사, 영업점, 온라인, 모바일앱.';

-- ---------------------------------------------------------------------
-- 4. mart.merchants — 가맹점정보
-- ---------------------------------------------------------------------
CREATE TABLE mart.merchants (
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

    CONSTRAINT pk_merchants PRIMARY KEY (merchant_id),
    CONSTRAINT uq_merchants_brn UNIQUE (business_registration_number),
    CONSTRAINT ck_merchants_open_month CHECK (merchant_open_month ~ '^\d{4}-\d{2}$'),
    CONSTRAINT ck_merchants_fee_tier CHECK (fee_tier_code IN ('영세', '중소1', '중소2', '일반')),
    CONSTRAINT ck_merchants_status CHECK (merchant_status IN ('정상', '휴업', '폐업')),

    CONSTRAINT ck_merchants_status_changed CHECK (
        (merchant_status = '정상' AND merchant_status_changed_month IS NULL)
     OR (merchant_status <> '정상' AND merchant_status_changed_month ~ '^\d{4}-\d{2}$')
    ),

    CONSTRAINT ck_merchants_changed_after_open CHECK (
        merchant_status_changed_month IS NULL
     OR merchant_status_changed_month >= merchant_open_month
    ),

    -- 개업월이 미래일 수 없음 (배치/생성 스크립트 날짜 버그 안전망)
    CONSTRAINT ck_merchants_open_not_future CHECK (
        merchant_open_month <= to_char(now(), 'YYYY-MM')
    )
);

COMMENT ON TABLE mart.merchants IS
'국내 가맹점 마스터. 전부 서울 소재이며 해외 가맹점은 포함되지 않음
— 포트폴리오 데모가 직접 계약한 가맹점만 존재하므로, 해외 거래는 transactions.merchant_id가
NULL로 기록된다.
주의 — 거래 이력이 전혀 없는 가맹점도 존재하므로 INNER JOIN 시 누락됨.
mcc_name은 mcc_codes로 분리되었으므로 업종명이 필요하면 조인할 것.';

COMMENT ON COLUMN mart.merchants.merchant_id IS
'가맹점 가명 ID. PK. 형식 MER_+12자리 HEX.
포트폴리오 데모가 부여하는 내부 식별자이므로 해외 가맹점에는 존재하지 않는다.
원본 가맹점번호와 1:1 대응하나 역매핑 불가.';

COMMENT ON COLUMN mart.merchants.merchant_name IS
'가맹점 상호명. 법인 상호는 개인식별정보가 아니므로 원본 유지.
단, 개인사업자 상호에 대표자명이 포함될 경우 재식별 위험이 있어 데이터 갱신 시 재검토 필요.';

COMMENT ON COLUMN mart.merchants.business_registration_number IS
'사업자등록번호 가명 토큰. UNIQUE. 형식 BRN_+12자리 HEX.
개인사업자의 경우 사업자번호로 개인 식별이 가능하므로 가명처리 대상.';

COMMENT ON COLUMN mart.merchants.mcc_code IS
'업종 코드. mcc_codes.mcc_code 참조 (V2에서 FK로 강제).
V3 트리거가 이 가맹점에서 발생한 거래의 업종이 다르게 기록되는 것을 차단함.';

COMMENT ON COLUMN mart.merchants.franchise_hq_code IS
'프랜차이즈 본부 코드. 형식 FR+3자리 숫자.
NULL = 프랜차이즈 소속이 아닌 개인 가맹점 — 결측이 아니라 정상값(다수를 차지).
프랜차이즈 단위 집계 시 NULL 처리 필수.
주의 — 본부명·주소 등 부가 속성이 없어 코드값만 존재하므로 마스터 테이블로 분리하지 않음.';

COMMENT ON COLUMN mart.merchants.merchant_region IS
'가맹점 소재 지역. "서울특별시 " + 자치구 형식. customers.resident_region과 동일한 15개구.
시군구 단위까지만 존재 — 상권 단위 정밀 분석 불가(설계상 의도).';

COMMENT ON COLUMN mart.merchants.merchant_open_month IS
'가맹 계약월(사업자등록월). YYYY-MM 형식. CHECK 제약으로 형식 강제.
V3 트리거가 이 값보다 이른 거래를 차단함.';

COMMENT ON COLUMN mart.merchants.fee_tier_code IS
'카드 수수료율 구분. 값: 영세, 중소1, 중소2, 일반. CHECK 제약으로 강제.
여신전문금융업법상 영세·중소 가맹점 우대수수료 체계.
가맹점 규모의 대리 지표이기도 하여 단말기 대수 결정에 사용됨(영세 1대 ~ 일반 3대).';

COMMENT ON COLUMN mart.merchants.merchant_status IS
'가맹점 상태. 값: 정상, 휴업, 폐업. CHECK 제약으로 강제.
현재 영업 중인 가맹점만 집계하려면 이 컬럼으로 필터 필요.';

COMMENT ON COLUMN mart.merchants.merchant_status_changed_month IS
'가맹점 상태가 마지막으로 변경된 월. YYYY-MM 형식.
NULL = 상태가 정상이며 변경된 적이 없음 — 결측이 아니라 해당 없음.
  폐업: 되돌릴 수 없으므로 이 월 이후 거래는 존재할 수 없음 → V3 트리거가 차단.
  휴업: 재개업이 가능하므로 이 월 이후 거래가 정상적으로 존재할 수 있어 차단하지 않음.
(cards.card_status_changed_month와 동일한 설계)';

-- ---------------------------------------------------------------------
-- 5. mart.transactions — 결제내역
-- ---------------------------------------------------------------------
CREATE TABLE mart.transactions (
    transaction_id         VARCHAR(20)   NOT NULL,
    card_number_masked     VARCHAR(32)   NOT NULL,
    merchant_id            VARCHAR(32),
    mcc_code               INTEGER       NOT NULL,
    transaction_datetime   TIMESTAMPTZ   NOT NULL,
    approval_status        VARCHAR(10)   NOT NULL,
    decline_reason_code    VARCHAR(20),
    transaction_amount     NUMERIC(15,2) NOT NULL,
    currency_code          VARCHAR(3)    NOT NULL,
    krw_converted_amount   NUMERIC(15,2) NOT NULL,
    applied_exchange_rate  NUMERIC(12,4) NOT NULL,
    merchant_country_code  VARCHAR(2)    NOT NULL,
    installment_months     SMALLINT      NOT NULL,
    approval_channel       VARCHAR(20)   NOT NULL,
    pos_entry_mode         VARCHAR(20)   NOT NULL,
    auth_method            VARCHAR(20)   NOT NULL,
    ip_address             VARCHAR(20),
    device_id              VARCHAR(32),
    terminal_id            VARCHAR(32)   NOT NULL,

    CONSTRAINT pk_transactions PRIMARY KEY (transaction_id),

    -- 금액은 0보다 커야 함. 외화 소액 거래가 원화 환산 시 0원이 되는 것을 방지
    CONSTRAINT ck_tx_amount_positive CHECK (
        transaction_amount > 0 AND krw_converted_amount > 0
    ),

    -- 원화환산액은 거래금액 × 적용환율과 일치해야 함 (반올림 오차 1원 허용)
    CONSTRAINT ck_tx_krw_derived CHECK (
        abs(krw_converted_amount - transaction_amount * applied_exchange_rate) <= 1
    ),

    -- 승인 건에는 거절 사유가 없고, 거절 건에는 반드시 있어야 함
    CONSTRAINT ck_tx_decline_consistent CHECK (
        (approval_status = '거절' AND decline_reason_code IS NOT NULL)
     OR (approval_status = '승인' AND decline_reason_code IS NULL)
    ),

    -- ip_address와 device_id는 온라인 거래에만 존재하므로 항상 함께 있거나 함께 없어야 함
    CONSTRAINT ck_tx_ip_device_pair CHECK (
        (ip_address IS NULL) = (device_id IS NULL)
    ),

    -- 거래 매체에 따라 승인 채널이 결정됨
    CONSTRAINT ck_tx_channel_pos_match CHECK (
        (pos_entry_mode IN ('IC칩', 'NFC', '마그네틱') AND approval_channel = '오프라인')
     OR (pos_entry_mode = 'CNP(온라인입력)'            AND approval_channel = '온라인_PG')
     OR (pos_entry_mode = 'APP_QR'                     AND approval_channel = '앱카드')
    ),

    -- 거래 매체에 따라 가능한 인증 방식이 제한됨
    --   비대면(CNP)에서 서명은 불가능하고, 앱 결제(APP_QR)에서 서명도 불가능하다
    CONSTRAINT ck_tx_auth_pos_match CHECK (
        (pos_entry_mode IN ('IC칩', 'NFC', '마그네틱') AND auth_method IN ('PIN', '서명', '없음'))
     OR (pos_entry_mode = 'CNP(온라인입력)'            AND auth_method IN ('3D Secure', '없음'))
     OR (pos_entry_mode = 'APP_QR'                     AND auth_method IN ('생체인증', 'PIN'))
    ),

    -- 오프라인 거래에는 ip/device가 없어야 함
    CONSTRAINT ck_tx_offline_no_ip CHECK (
        approval_channel <> '오프라인' OR ip_address IS NULL
    ),

    CONSTRAINT ck_tx_installment CHECK (installment_months IN (0, 2, 3, 6, 12)),
    CONSTRAINT ck_tx_status CHECK (approval_status IN ('승인', '거절')),
    CONSTRAINT ck_tx_rate_positive CHECK (applied_exchange_rate > 0),

    -- 국내 가맹점 거래는 KR/KRW 여야 함
    CONSTRAINT ck_tx_domestic CHECK (
        merchant_id IS NULL
     OR (merchant_country_code = 'KR' AND currency_code = 'KRW')
    ),

    -- 거래일시가 미래일 수 없음 (2026-07 실측: 이 제약 없이는 2030년 거래도 적재됨.
    -- now()는 비불변 함수라 CHECK에 쓰면 덤프/복원 시 재검증되지만,
    -- 과거 시각은 시간이 지나도 계속 과거이므로 이 제약에선 안전함)
    CONSTRAINT ck_tx_not_future CHECK (
        transaction_datetime <= now()
    )
);

COMMENT ON TABLE mart.transactions IS
'카드 결제 승인/거절 내역.
국내거래(merchant_id 존재, 전부 KR/KRW)와 해외거래(merchant_id NULL)로 이분됨.
고객 정보가 필요하면 cards를 경유해 조인할 것 — customer_id를 중복 저장하지 않음.
가명화 데이터 중 유일하게 시각(초 단위)이 원본 그대로 남아 있음 — 내부 활용 기준.
외부 제공 시 일 단위 일반화 검토 필요.

거래 밀도: 가맹점당 월 6건 수준으로 실제 카드 거래 밀도보다 현저히 낮음.
검증/파이프라인 개발용 규모이며, 절대량 기반 해석(매출 규모, 단말기 처리량 등)에는
부적합. 비율·분포 기반 분석(연령대별 소비 비중, 이상거래 패턴 등)은 유효.

의도적으로 포함된 이상거래 패턴(부정사용 탐지 모델 학습용):
  - 자체FDS의심차단 건은 심야 시간대·고액·무인증 특성을 가짐
  - 동일 고객이 같은 날 국내와 해외에서 오프라인 결제한 사례가 존재
    (물리적으로 불가능하므로 카드 복제 의심 신호이며, FDS 미탐지 사례로 남겨둠)';

COMMENT ON COLUMN mart.transactions.transaction_id IS
'거래 승인번호/일련번호. PK. 형식 TX+8자리 숫자.
개인식별정보가 아니므로 원본 유지.';

COMMENT ON COLUMN mart.transactions.card_number_masked IS
'거래에 사용된 카드. NOT NULL. cards.card_number_masked 참조 (V2에서 FK로 강제).
고객 정보는 이 컬럼으로 cards를 조인해 얻는다.';

COMMENT ON COLUMN mart.transactions.merchant_id IS
'거래가 발생한 가맹점. merchants.merchant_id 참조 (V2에서 FK로 강제).
NULL = 해외 거래 등 국내 가맹점 마스터에 없는 건 — 결측이 아니라 정상값(약 1/4을 차지).
merchant_id가 있는 거래는 전부 국내(KR/KRW) — ck_tx_domestic이 강제.
주의 — 가맹점 정보가 필요한 집계에서 INNER JOIN 시 해외거래가 통째로 제외되므로 의도 확인 필요.';

COMMENT ON COLUMN mart.transactions.mcc_code IS
'거래 업종 코드. mcc_codes.mcc_code 참조 (V2에서 FK로 강제).
merchant_id가 NULL이어도 값이 존재하는 이유 — MCC는 가맹점 마스터가 아니라
카드 승인 전문(국제 카드망이 전달)에서 오기 때문. 해외 가맹점도 MCC는 부여받는다.
따라서 merchant_id로 조인해서 얻을 수 있는 중복 정보가 아니다.
국내 가맹점이 없는 업종(해외 교통/승차공유 등)은 merchants에 대응 행이 없으나
mcc_codes에는 정의되어 있으므로 업종명 조회가 가능하다.
주의 — 특정 MCC를 해외거래 판별 기준으로 쓰지 말 것.
해외 판별은 merchant_id IS NULL 또는 merchant_country_code <> ''KR''로 할 것.
V3 트리거가 국내거래의 MCC 불일치를 차단함.';

COMMENT ON COLUMN mart.transactions.transaction_datetime IS
'승인 일시. 초 단위까지 원본 유지. TIMESTAMPTZ 타입이나 원본에 시간대 정보가 없어
적재 시 서버 시간대로 해석됨 — KST 기준으로 간주할 것.
V3 트리거가 카드 발급월·가맹점 개업월보다 이른 거래, 카드 해지월·가맹점 폐업월보다
이후 거래를 차단함.';

COMMENT ON COLUMN mart.transactions.approval_status IS
'승인 결과. 값: 승인, 거절. CHECK 제약으로 강제.
주의 — 매출 집계 시 반드시 승인 건만 필터할 것.
거절 건에도 시도 금액과 할부 개월이 기록되어 있음(승인 요청 시점의 조건이므로 정상).';

COMMENT ON COLUMN mart.transactions.decline_reason_code IS
'거절 사유. 값: 자체FDS의심차단, 비밀번호오류, 한도초과, 유효기간만료,
카드정지_분실도난, 잔액부족, 가맹점거래거절.
NULL = 승인된 거래 — 결측이 아니라 해당 없음.
approval_status와의 정합성은 ck_tx_decline_consistent가 강제함.
자체FDS의심차단 건은 이상거래 패턴(심야·고액·무인증)을 가지므로
부정사용 탐지 모델의 학습 라벨로 사용 가능.';

COMMENT ON COLUMN mart.transactions.transaction_amount IS
'승인 금액 (거래 통화 기준, currency_code 참조).
통화별 현실적 범위에서 생성됨 — 통화마다 화폐 단위가 다르므로 금액 스케일도 다름.
주의 — 통화가 여러 종이므로 통화 구분 없이 SUM하면 무의미(엔화와 원화가 그대로 합산됨).
금액 집계에는 krw_converted_amount를 사용할 것.
고액 거래는 상단코딩(cap) 적용 — 동일한 최대값이 반복되는 행은 실제 금액이 아님.';

COMMENT ON COLUMN mart.transactions.currency_code IS
'거래 통화 (ISO 4217). 값: KRW, USD, EUR, JPY, THB, SGD, PHP, VND.
KRW는 전부 국내거래. ck_tx_domestic이 국내 가맹점 거래의 외화 결제를 차단.';

COMMENT ON COLUMN mart.transactions.krw_converted_amount IS
'원화 환산 금액 = transaction_amount × applied_exchange_rate.
금액 집계·비교 시 이 컬럼을 사용 — 통화 중립적.
ck_tx_krw_derived가 파생식 일치를 강제함(반올림 오차 1원 허용).
ck_tx_amount_positive가 0원 이하를 차단 — 외화 소액 거래가 환산 시 0원으로
반올림되는 것을 방지하기 위해 원본 금액을 통화별 최소 화폐단위로 맞춰 생성함.
NUMERIC 타입이므로 부동소수점 누적 오차 없음.';

COMMENT ON COLUMN mart.transactions.applied_exchange_rate IS
'거래 시점 적용 환율 (1 외화 = N원). KRW는 1.0.
통화가 정해지면 환율이 결정되는 것처럼 보이나, 이는 합성 데이터의 특성.
실제 환율은 일별로 변동하므로 거래 시점 스냅샷으로 저장하는 것이 정상 — 중복 컬럼이 아님.
주의 — 현재 데이터는 통화별 고정값이라 기간 중 환율 변동이 없음. 시계열 환율 분석 불가.';

COMMENT ON COLUMN mart.transactions.merchant_country_code IS
'가맹점 소재 국가 (ISO 3166-1 alpha-2). 값: KR, US, JP, FR, IT, TH, SG, PH, VN.
KR = 국내거래(merchant_id 존재), 그 외 = 해외거래(merchant_id NULL).
주의 — 국가와 통화는 1:1이 아님. FR·IT가 EUR을 공유.';

COMMENT ON COLUMN mart.transactions.installment_months IS
'할부 개월 수. 값: 0(일시불), 2, 3, 6, 12. CHECK 제약으로 강제. 대부분 일시불.
V3 트리거가 체크카드의 할부 거래를 차단함 — 체크카드는 즉시 출금되므로 할부가 성립하지 않음.';

COMMENT ON COLUMN mart.transactions.approval_channel IS
'승인 채널. 값: 오프라인, 온라인_PG, 앱카드.
pos_entry_mode에 의해 결정되는 중복 컬럼이나, 조인 없이 채널별 집계를 하기 위해 유지.
ck_tx_channel_pos_match가 대응 관계를 강제하므로 불일치가 발생할 수 없음.';

COMMENT ON COLUMN mart.transactions.pos_entry_mode IS
'거래 매체(카드 정보 입력 방식). 값: IC칩, NFC, 마그네틱, APP_QR, "CNP(온라인입력)".
주의 — CNP 값 자체에 괄호가 포함된 문자열이므로 비교 시 정확히 ''CNP(온라인입력)''로 매칭할 것.
CNP = Card Not Present(비대면). 마그네틱은 복제 위험이 높아 부정사용 분석의 주요 지표.
approval_channel·auth_method가 이 값에 의해 제한됨 — CHECK 제약으로 강제.';

COMMENT ON COLUMN mart.transactions.auth_method IS
'본인인증 방식. 값: 서명, PIN, 생체인증, 3D Secure, 없음.
pos_entry_mode에 따라 가능한 값이 제한됨 — ck_tx_auth_pos_match가 강제.
(비대면 거래에서 서명은 불가능하고, 앱 결제에서도 서명은 불가능)
"없음"은 무인증 거래 — 부정사용 위험 분석 시 주목 대상.';

COMMENT ON COLUMN mart.transactions.ip_address IS
'접속 IP. /24 대역 단위 (예: 123.45.67.0/24). 개별 접속자 특정 불가(설계상 의도).
NULL = 오프라인 거래 — 결측이 아니라 해당 없음(대다수를 차지).
주의 — CIDR 표기 문자열이며 inet/cidr 타입이 아님. 네트워크 연산자 사용 불가.
device_id와 항상 함께 존재하거나 함께 없음(ck_tx_ip_device_pair가 강제).';

COMMENT ON COLUMN mart.transactions.device_id IS
'거래 기기 가명 토큰. 형식 DEV_+12자리 HEX.
NULL = 오프라인 거래 — ip_address와 동일한 NULL 패턴.
동일 기기의 반복 거래 추적에 사용 가능.';

COMMENT ON COLUMN mart.transactions.terminal_id IS
'결제 단말기 가명 토큰. 형식 TERM_+12자리 HEX. NOT NULL.
오프라인 거래는 가맹점에 설치된 물리 단말기(가맹점 규모에 따라 1~3대),
온라인 거래는 PG사가 발급한 가상 단말기를 가리킨다.
따라서 하나의 단말기에 여러 카드의 거래가 누적되며, 이를 이용해
"동일 단말기에서 단시간에 다수의 카드가 사용"되는 카드 복제 패턴을 탐지할 수 있다.';

-- 인덱스: FK 조인용 최소한만 생성 (약 1.2만 행)
CREATE INDEX idx_tx_card     ON mart.transactions (card_number_masked);
CREATE INDEX idx_tx_merchant ON mart.transactions (merchant_id);
CREATE INDEX idx_tx_datetime ON mart.transactions (transaction_datetime);
-- 업종별 필터/집계가 핵심 분석 축. 2026-07 EXPLAIN 실측: 인덱스 없으면 Seq Scan
-- (12,477건 기준 1.4ms → 인덱스 후 0.29ms, 규모가 커질수록 격차 확대)
CREATE INDEX idx_tx_mcc      ON mart.transactions (mcc_code);
