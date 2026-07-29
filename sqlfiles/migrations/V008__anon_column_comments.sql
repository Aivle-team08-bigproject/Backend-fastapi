-- =====================================================================
-- [V008] anonymized 스키마 — 테이블/컬럼 COMMENT 전면 정비
-- 대상: PostgreSQL 17, portfolio DB
--
-- 실행: psql -v ON_ERROR_STOP=1 -U portfolio_admin -d portfolio \
--             -f migrations/V008__anon_column_comments.sql
--   ※ COMMENT는 소유자만 변경 가능 → 반드시 portfolio_admin으로 실행.
--   ※ 멱등: COMMENT는 항상 덮어쓰기이므로 몇 번 재실행해도 결과가 같다.
-- 이전: V007 (익명처리 이력)
--
-- ---------------------------------------------------------------------
-- 왜 이 파일이 필요한가
-- ---------------------------------------------------------------------
-- agent_svc는 anonymized 스키마의 테이블/컬럼 COMMENT를 읽을 수 있고, 그 텍스트가
-- 스키마-온리 프롬프팅을 통해 LLM 컨텍스트에 그대로 들어간다.
-- 즉 anonymized의 COMMENT는 "DBA용 설계 메모"가 아니라 "LLM에게 주는 분석 가이드"다.
--
-- 작성 원칙
--   쓸 것    : 컬럼의 의미 / 값의 종류·형식 / NULL의 의미 /
--              조인·집계 시 함정 / 분석 축으로 쓸 수 있는지
--   쓰지 말 것: 익명처리 방법, 목표 k값, 손실 비율, 어떤 행이 처리 대상이었는지,
--              해시·salt 방식, 상위 계층과의 대응 관계
--              → 전부 재식별 공격의 단서이며, 분리 보관 원칙에도 어긋난다.
--              설계 근거는 mart 코멘트와 문서에만 둔다(사람만 조회).
--
-- COMMENT ON SCHEMA는 GRANT와 무관하게 누구나 조회 가능하므로(2026-07 실측)
-- 스키마 레벨에는 개요만 남기고 상세는 테이블/컬럼에 둔다.
--
-- 값의 종류는 2026-07-28 실제 적재 데이터 기준으로 확인해 기재했다.
-- =====================================================================

BEGIN;

-- =====================================================================
-- 0. 스키마
-- =====================================================================
COMMENT ON SCHEMA anonymized IS
'분석용 카드 데이터. AI 에이전트가 조회하는 영역이며 개인을 특정할 수 있는 정보는 없다.

구성: mcc_codes(업종) / customers(고객) / cards(카드) / merchants(가맹점) /
      transactions(결제내역) 5개 테이블. 모든 FK는 이 스키마 안에서 완결된다.

조인 경로: transactions → cards → customers (거래에 customer_id 없음),
           transactions → merchants → mcc_codes.
데이터 범위: 국내 가맹점은 전부 서울, 고객 거주지도 서울.

세부 의미·주의사항은 각 테이블/컬럼 COMMENT를 볼 것.';


-- =====================================================================
-- 1. anonymized.mcc_codes
-- =====================================================================
COMMENT ON TABLE anonymized.mcc_codes IS
'업종 코드 마스터 (Merchant Category Code, ISO 18245 국제 표준). 1행 = 1업종.
merchants와 transactions 양쪽에서 참조한다.
국내 가맹점이 없는 업종(해외 교통/승차공유 등)도 정의돼 있으므로,
해외 거래도 이 테이블로 업종명을 얻을 수 있다.';

COMMENT ON COLUMN anonymized.mcc_codes.mcc_code IS
'업종 코드. PK. ISO 18245 표준 코드값.';

COMMENT ON COLUMN anonymized.mcc_codes.mcc_name IS
'업종명. 코드와 1:1 대응.
값(16종): 음식점, 편의점, 마트/슈퍼마켓, 백화점, 주유소, 약국, 병원, 서점,
학원/교육서비스, 미용실, 영화관, 헬스장/스포츠클럽, 호텔/숙박, 여행사,
부동산중개, 해외 교통/승차공유.';


-- =====================================================================
-- 2. anonymized.customers
-- =====================================================================
COMMENT ON TABLE anonymized.customers IS
'고객 마스터. 1행 = 1고객. 사람의 속성만 있고 카드 속성은 cards에 있다.
카드는 cards.customer_id로, 거래는 cards를 경유해 transactions로 조인한다.
고객:카드는 구조상 1:N이다(현재 데이터는 1:1).';

COMMENT ON COLUMN anonymized.customers.customer_id IS
'고객 식별자. PK. 형식 ACUST_+12자리 HEX.
이 데이터셋 안에서만 유효한 값이며 실제 고객번호와는 무관하다.
고객 단위 집계·중복제거는 이 컬럼으로 하면 된다.
cards.customer_id가 참조.';

COMMENT ON COLUMN anonymized.customers.gender IS
'성별. 값: M, F. 두 값만 존재한다(NULL 없음).';

COMMENT ON COLUMN anonymized.customers.age_band IS
'연령대. 값: 10대, 20대, 30대, 40대, 50대, 60대, 70대 이상.
10세 단위 범주값만 존재하며 정확한 나이·생년월일은 이 데이터셋에 없다.
occupation과 논리적으로 정합함(예: 10대는 학생·서비스직·프리랜서만 존재).
주의 — 문자열이므로 ORDER BY 시 사전순 정렬된다("70대 이상"이 마지막에 오지 않음).
연령 순서가 필요하면 CASE로 순번을 부여할 것.';

COMMENT ON COLUMN anonymized.customers.resident_region IS
'거주 지역. 값의 형태가 두 가지이므로 GROUP BY 전에 반드시 확인할 것.
  (a) "서울특별시 " + 자치구 — 15개구
      (강남/강서/관악/광진/구로/노원/동작/마포/서초/성동/송파/영등포/용산/은평/종로)
  (b) "서울특별시" — 자치구 정보 없이 시 단위까지만 기록된 행
자치구별 집계를 할 때 (b)가 별도 그룹으로 잡히므로, 자치구 분석에서는
(b)를 "미상"으로 취급하거나 제외할지 먼저 정할 것.
동 단위 이하 및 좌표 분석은 불가능하다.';

COMMENT ON COLUMN anonymized.customers.postal_code IS
'우편번호. 형태가 두 가지다: 앞 3자리 + "**" (예: 061**), 앞 1자리 + "****" (예: 0****).
resident_region과 항상 정합한다 — 자치구가 기록된 행은 3자리 형태,
시 단위까지만 기록된 행은 1자리 형태를 가진다.
주의 — resident_region의 완전한 중복 정보가 아니다. 한 자치구에 여러 우편번호
대역이 있어 3자리 형태는 지역보다 세밀하다. 다만 지역 축 분석은 resident_region을
쓰는 편이 안전하다(형태가 섞여 있어 문자열 비교가 어긋나기 쉬움).';

COMMENT ON COLUMN anonymized.customers.occupation IS
'직업. 값: 사무직, 자영업, 전문직, 서비스직, 프리랜서, 공무원, 학생, 무직/은퇴.
age_band에 따라 가능한 값이 제한된다 — 연령대별 직업 분포를 반영한 데이터.';

COMMENT ON COLUMN anonymized.customers.annual_income_band IS
'연소득 구간. 1천만원 단위, "N~M원" 형식 (예: 30,000,000~39,999,999원).
현재 11개 구간이 존재하며 정확한 소득액은 이 데이터셋에 없다.
occupation·age_band와 정합함(예: 학생은 하위 구간, 40~50대 전문직은 상위 구간).
주의 — 문자열이므로 ORDER BY 시 사전순으로 정렬된다
(1억원대가 2천만원대보다 앞에 옴). 크기순 정렬이 필요하면 앞 숫자를 파싱할 것.';

COMMENT ON COLUMN anonymized.customers.marital_status IS
'결혼 여부. 값: 기혼, 미혼. 10~20대는 미혼으로 고정.';


-- =====================================================================
-- 3. anonymized.cards
-- =====================================================================
COMMENT ON TABLE anonymized.cards IS
'카드 마스터. 1행 = 1카드. 고객:카드 = 1:N 구조(현재 데이터는 1:1).
transactions는 이 테이블을 경유해야 고객에 도달한다(거래에 customer_id 없음).';

COMMENT ON COLUMN anonymized.cards.card_number_masked IS
'카드 식별자. PK. 형식 CARD_+12자리 HEX.
이 데이터셋 안에서만 유효한 값이며 실제 카드번호와는 무관하다.
transactions.card_number_masked가 참조.';

COMMENT ON COLUMN anonymized.cards.customer_id IS
'카드 소유 고객. customers.customer_id 참조.
거래에서 고객 속성(연령대·지역 등)이 필요하면
transactions → cards → customers 순으로 조인한다.';

COMMENT ON COLUMN anonymized.cards.card_product_code IS
'카드 상품 코드. 값: CR-PT001, CR-MI002, CR-CB003(신용), CK-MB101, CK-YT102(체크).
접두 CR = 신용카드, CK = 체크카드 — 신용/체크 구분은 LIKE ''CR%'' / ''CK%''로 한다.
체크카드는 즉시 출금이므로 할부 거래가 존재하지 않는다.';

COMMENT ON COLUMN anonymized.cards.card_issue_month IS
'카드 발급 월. YYYY-MM 형식 문자열. 월 단위까지만 존재한다.
이 값보다 이른 시점의 거래는 존재하지 않는다.
문자열이지만 YYYY-MM은 사전순 = 시간순이므로 그대로 비교·정렬해도 된다.';

COMMENT ON COLUMN anonymized.cards.credit_limit_band IS
'카드 승인한도 구간. 값: 500만원 미만, 500~1,000만원, 1,000~2,000만원,
2,000~3,000만원, 3,000만원 이상. 정확한 한도액은 이 데이터셋에 없다.
고객의 소득 구간에 따라 상한이 결정된다(여신 심사 기준 반영).
체크카드는 신용공여가 없어 낮은 구간만 부여된다.
주의 — 문자열이므로 크기순 정렬이 필요하면 CASE로 순번을 부여할 것.';

COMMENT ON COLUMN anonymized.cards.card_status IS
'카드 상태. 값: 정상, 일시정지, 해지.
조회 시점의 현재 상태이며, 변경 시점은 card_status_changed_month를 볼 것.';

COMMENT ON COLUMN anonymized.cards.card_status_changed_month IS
'카드 상태가 마지막으로 변경된 월. YYYY-MM 형식.
NULL = 상태가 정상이며 변경된 적이 없음 — 결측이 아니라 해당 없음.
주의 — 최종 변경 시점만 있고 상태 변경 "이력"은 없다. 따라서 과거 특정 시점의
카드 상태를 정확히 판정할 수는 없다.
  해지: 되돌릴 수 없으므로 이 월 이후 거래가 존재하지 않는다.
  일시정지: 해제 후 재사용이 가능하므로 이 월 이후 거래가 정상적으로 존재할 수 있다.';

COMMENT ON COLUMN anonymized.cards.signup_channel IS
'카드 가입 경로. 값: 제휴사, 영업점, 온라인, 모바일앱.
거래의 approval_channel(승인 채널)과는 다른 개념이니 혼동하지 말 것.';


-- =====================================================================
-- 4. anonymized.merchants
-- =====================================================================
COMMENT ON TABLE anonymized.merchants IS
'국내 가맹점 마스터. 1행 = 1가맹점. 전부 서울 소재이며 해외 가맹점은 없다
— 해외 거래는 transactions.merchant_id가 NULL로 기록된다.
주의 — 거래 이력이 전혀 없는 가맹점도 존재하므로 INNER JOIN 시 누락된다.
업종명이 필요하면 mcc_codes를 조인할 것.';

COMMENT ON COLUMN anonymized.merchants.merchant_id IS
'가맹점 식별자. PK. 형식 MER_+12자리 HEX.
카드사 내부 식별자에 해당하므로 해외 가맹점에는 존재하지 않는다.
transactions.merchant_id가 참조.';

COMMENT ON COLUMN anonymized.merchants.merchant_name IS
'가맹점 상호명. 형식 MERCH_+12자리 HEX.
실제 상호가 아닌 대체 표기이므로 브랜드·업종을 이름에서 유추할 수 없다.
업종 분석은 mcc_code로, 프랜차이즈 분석은 franchise_hq_code로 할 것.
가맹점을 세는 용도로는 merchant_id를 쓴다.';

COMMENT ON COLUMN anonymized.merchants.business_registration_number IS
'사업자등록번호 대체값. 형식 BRN_+12자리 HEX. 가맹점과 1:1 대응.
실제 사업자번호가 아니므로 외부 데이터와 대조할 수 없다.
merchant_id가 이미 PK이므로 이 컬럼을 조인 키로 쓸 이유는 없다.';

COMMENT ON COLUMN anonymized.merchants.mcc_code IS
'업종 코드. mcc_codes.mcc_code 참조.
이 가맹점에서 발생한 국내 거래의 transactions.mcc_code와 항상 일치한다.';

COMMENT ON COLUMN anonymized.merchants.franchise_hq_code IS
'프랜차이즈 본부 코드. 형식 FR + 3자리 숫자.
NULL = 프랜차이즈 소속이 아닌 개인 가맹점 — 결측이 아니라 정상값이며 다수(150개 중 109개)를 차지.
프랜차이즈 단위 집계 시 NULL 처리를 반드시 명시할 것.
본부명·주소 등 부가 속성은 없고 코드값만 존재한다.';

COMMENT ON COLUMN anonymized.merchants.merchant_region IS
'가맹점 소재 지역. "서울특별시 " + 자치구 형식. 15개구 전부 값이 있다.
customers.resident_region과 같은 15개구를 쓰지만, 그쪽과 달리 시 단위 값은 없으므로
가맹점 지역 축은 그대로 GROUP BY 해도 된다.
상권·동 단위 정밀 분석은 불가능하다.';

COMMENT ON COLUMN anonymized.merchants.merchant_open_month IS
'가맹 계약월(사업자등록월). YYYY-MM 형식.
이 값보다 이른 시점의 거래는 존재하지 않는다.';

COMMENT ON COLUMN anonymized.merchants.fee_tier_code IS
'카드 수수료율 구분. 값: 영세, 중소1, 중소2, 일반.
여신전문금융업법상 영세·중소 가맹점 우대수수료 체계.
가맹점 규모의 대리 지표로도 쓸 수 있다(영세일수록 소규모).';

COMMENT ON COLUMN anonymized.merchants.merchant_status IS
'가맹점 상태. 값: 정상, 휴업, 폐업.
현재 영업 중인 가맹점만 집계하려면 이 컬럼으로 필터할 것.';

COMMENT ON COLUMN anonymized.merchants.merchant_status_changed_month IS
'가맹점 상태가 마지막으로 변경된 월. YYYY-MM 형식.
NULL = 상태가 정상이며 변경된 적이 없음 — 결측이 아니라 해당 없음.
  폐업: 되돌릴 수 없으므로 이 월 이후 거래가 존재하지 않는다.
  휴업: 재개업이 가능하므로 이 월 이후 거래가 정상적으로 존재할 수 있다.
(cards.card_status_changed_month와 동일한 설계)';


-- =====================================================================
-- 5. anonymized.transactions
-- =====================================================================
COMMENT ON TABLE anonymized.transactions IS
'카드 결제 승인/거절 내역. 1행 = 1승인요청. 이 스키마의 사실(fact) 테이블.
국내거래(merchant_id 존재, 전부 KR/KRW)와 해외거래(merchant_id NULL)로 이분된다.
고객 속성이 필요하면 cards를 경유해 조인할 것 — 이 테이블에 customer_id는 없다.

집계 시 먼저 확인할 것
  1) 매출·거래액 집계는 approval_status = ''승인''만 필터할 것(거절 건에도 금액이 있음).
  2) 통화가 8종이므로 금액 합계는 krw_converted_amount로 할 것.
  3) 금액은 근사값으로 기록돼 있어 절대 매출액이 아니다. 비교·구성비·추세 분석용.
  4) 거래 밀도가 실제 카드 거래보다 훨씬 낮으므로(가맹점당 월 6건 수준)
     "매출 규모", "단말기 처리량" 같은 절대량 해석에는 부적합하다.
     비율·분포 기반 분석(연령대별 소비 비중, 이상거래 패턴 등)은 유효하다.

부정사용 탐지용으로 의도적으로 포함된 패턴
  - 자체FDS의심차단 건은 심야 시간대·고액·무인증 특성을 가진다.
  - 동일 고객이 같은 날 국내와 해외에서 오프라인 결제한 사례가 있다
    (물리적으로 불가능 → 카드 복제 의심 신호. FDS 미탐지 사례로 남겨둔 것).';

COMMENT ON COLUMN anonymized.transactions.transaction_id IS
'거래 일련번호. PK. 형식 TX + 8자리 숫자. 거래 건수는 이 컬럼으로 센다.';

COMMENT ON COLUMN anonymized.transactions.card_number_masked IS
'거래에 사용된 카드. NOT NULL. cards.card_number_masked 참조.
고객 정보는 이 컬럼으로 cards를 조인해 얻는다.';

COMMENT ON COLUMN anonymized.transactions.merchant_id IS
'거래가 발생한 가맹점. merchants.merchant_id 참조.
NULL = 국내 가맹점 마스터에 없는 건(해외 거래) — 결측이 아니라 정상값이며 약 1/4을 차지.
merchant_id가 있는 거래는 전부 국내(KR/KRW)다.
주의 — 가맹점 정보가 필요한 집계에서 INNER JOIN 하면 해외거래가 통째로 빠지므로,
전체 거래를 대상으로 하려면 LEFT JOIN 할 것.';

COMMENT ON COLUMN anonymized.transactions.mcc_code IS
'거래 업종 코드. mcc_codes.mcc_code 참조. 해외거래에도 항상 값이 있다.
merchant_id가 NULL이어도 값이 있는 이유 — MCC는 가맹점 마스터가 아니라
카드 승인 전문(국제 카드망)에서 오기 때문. 해외 가맹점도 MCC를 부여받는다.
따라서 merchants를 조인해서 얻을 수 있는 중복 정보가 아니며,
업종별 분석은 merchants가 아니라 이 컬럼을 기준으로 해야 해외거래까지 포함된다.
국내 거래에서는 merchants.mcc_code와 항상 일치한다.
주의 — 특정 MCC를 해외거래 판별 기준으로 쓰지 말 것.
해외 판별은 merchant_id IS NULL 또는 merchant_country_code <> ''KR''로 한다.';

COMMENT ON COLUMN anonymized.transactions.transaction_datetime IS
'승인 일시. TIMESTAMPTZ. KST 기준으로 해석할 것.
시각은 3시간 간격의 정각값만 존재한다(00, 03, 06, 09, 12, 15, 18, 21시)
— 분·초는 항상 0이다. 따라서
  가능: 날짜·요일·월 단위 추세, 심야/주간 등 시간대 구간별 분석
  불가능: 시간 단위(예: 오후 2시대) 분석, 분 단위 간격·체류시간 분석
시간대 축으로 GROUP BY 할 때는 date_trunc(''hour'', ...) 대신
extract(hour from ...)를 그대로 쓰면 8개 구간으로 깔끔하게 나뉜다.';

COMMENT ON COLUMN anonymized.transactions.approval_status IS
'승인 결과. 값: 승인, 거절.
주의 — 매출 집계 시 반드시 승인 건만 필터할 것.
거절 건에도 시도 금액과 할부 개월이 기록돼 있다(승인 요청 시점의 조건이므로 정상).
승인율 = 승인 건수 / 전체 건수로 계산 가능.';

COMMENT ON COLUMN anonymized.transactions.decline_reason_code IS
'거절 사유. 값: 자체FDS의심차단, 비밀번호오류, 한도초과, 유효기간만료,
카드정지_분실도난, 잔액부족, 가맹점거래거절.
NULL = 승인된 거래 — 결측이 아니라 해당 없음. approval_status와 항상 정합한다.
자체FDS의심차단 건은 이상거래 패턴(심야·고액·무인증)을 가지므로
부정사용 탐지 분석의 라벨로 사용할 수 있다.';

COMMENT ON COLUMN anonymized.transactions.transaction_amount IS
'승인 금액 (거래 통화 기준 — currency_code를 함께 볼 것).
주의 1 — 통화가 여러 종이므로 통화 구분 없이 SUM하면 무의미하다
(엔화와 원화가 그대로 합산됨). 금액 집계에는 krw_converted_amount를 쓸 것.
주의 2 — 근사값으로 기록돼 있어 개별 거래의 정확한 결제액이 아니다.
구간·구성비·순위 비교에는 유효하나 "정확히 얼마"를 답하는 데는 쓰지 말 것.';

COMMENT ON COLUMN anonymized.transactions.currency_code IS
'거래 통화 (ISO 4217). 값: KRW, USD, EUR, JPY, THB, SGD, PHP, VND.
KRW는 전부 국내거래다.';

COMMENT ON COLUMN anonymized.transactions.krw_converted_amount IS
'원화 환산 금액 = transaction_amount × applied_exchange_rate.
금액 집계·비교는 통화 중립적인 이 컬럼으로 할 것.
NUMERIC 타입이라 부동소수점 누적 오차는 없다.
주의 — transaction_amount와 마찬가지로 근사값이므로 총액의 자릿수까지 신뢰하지 말 것.';

COMMENT ON COLUMN anonymized.transactions.applied_exchange_rate IS
'거래 시점 적용 환율 (1 외화 = N원). KRW는 1.0.
주의 — 현재 데이터는 통화별 고정값이라 기간 중 환율 변동이 없다. 시계열 환율 분석 불가.
실제로는 거래 시점 스냅샷을 저장하는 것이 정상이므로 중복 컬럼이 아니다.';

COMMENT ON COLUMN anonymized.transactions.merchant_country_code IS
'가맹점 소재 국가 (ISO 3166-1 alpha-2). 값: KR, US, JP, FR, IT, TH, SG, PH, VN.
KR = 국내거래(merchant_id 존재), 그 외 = 해외거래(merchant_id NULL).
주의 — 국가와 통화는 1:1이 아니다. FR·IT가 EUR을 공유한다.';

COMMENT ON COLUMN anonymized.transactions.installment_months IS
'할부 개월 수. 값: 0(일시불), 2, 3, 6, 12. 대부분 일시불.
할부 거래만 보려면 installment_months > 0으로 필터할 것.
체크카드(card_product_code가 CK로 시작)에는 할부 거래가 존재하지 않는다.';

COMMENT ON COLUMN anonymized.transactions.approval_channel IS
'승인 채널. 값: 오프라인, 온라인_PG, 앱카드.
pos_entry_mode에 의해 결정되는 파생 컬럼이지만, 조인 없이 채널별 집계를 하도록 유지한다.
둘의 대응 관계는 항상 정합하므로 채널 축 집계는 이 컬럼만 써도 된다.
cards.signup_channel(카드 가입 경로)과는 다른 개념이다.';

COMMENT ON COLUMN anonymized.transactions.pos_entry_mode IS
'거래 매체(카드 정보 입력 방식). 값: IC칩, NFC, 마그네틱, APP_QR, CNP(온라인입력).
주의 — 마지막 값은 괄호를 포함한 문자열 그대로이므로 ''CNP(온라인입력)''로 정확히 매칭할 것.
CNP = Card Not Present(비대면).
마그네틱은 복제 위험이 높아 부정사용 분석의 주요 지표다.
approval_channel·auth_method가 이 값에 의해 제한된다.';

COMMENT ON COLUMN anonymized.transactions.auth_method IS
'본인인증 방식. 값: 서명, PIN, 생체인증, 3D Secure, 없음.
pos_entry_mode에 따라 가능한 값이 제한된다
(비대면·앱 결제에서는 서명이 불가능하다).
"없음"은 무인증 거래로, 부정사용 위험 분석의 주목 대상이다.';

COMMENT ON COLUMN anonymized.transactions.ip_address IS
'접속 IP의 /24 대역 (예: 84.170.71.0/24). 개별 접속자는 특정할 수 없다.
NULL = 오프라인 거래 — 결측이 아니라 해당 없음이며 대다수를 차지한다.
주의 — CIDR 표기 "문자열"이며 inet/cidr 타입이 아니다. 네트워크 연산자를 쓸 수 없고
대역 비교는 문자열 매칭으로 해야 한다.
device_frequency_band와 항상 함께 존재하거나 함께 없다.';

COMMENT ON COLUMN anonymized.transactions.device_frequency_band IS
'이 거래에 사용된 기기가 데이터셋 전체에서 몇 번 등장하는지의 구간.
값: 1회, 2~5회, 6회 이상. 기기 자체를 특정하는 값은 아니다.
용도 — "특정 기기에 거래가 몰리는가"를 개별 기기 추적 없이 판단하는 축.
6회 이상 + 무인증 + 심야 조합은 부정사용 의심 신호로 볼 수 있다.
NULL = 오프라인 거래(ip_address와 동일한 NULL 패턴).
주의 — 이 컬럼으로 GROUP BY 해도 "기기 수"는 셀 수 없다. 거래 건수만 셀 수 있다.';

COMMENT ON COLUMN anonymized.transactions.terminal_frequency_band IS
'이 거래가 발생한 결제 단말기가 데이터셋 전체에서 몇 번 등장하는지의 구간.
값: 1~5회, 6~15회, 16회 이상. NOT NULL(모든 거래에 단말기가 있다).
오프라인은 가맹점의 물리 단말기, 온라인은 PG사의 가상 단말기를 가리킨다.
용도 — "한 단말기에 거래가 비정상적으로 몰린다"(카드 복제 의심)를
단말기 식별 없이 판단하는 축.
주의 — 이 컬럼으로 GROUP BY 해도 "단말기 수"는 셀 수 없다. 거래 건수만 셀 수 있다.';

COMMIT;

-- =====================================================================
-- 검증 (실행 후 아래 두 쿼리가 모두 0건이어야 한다)
-- =====================================================================
-- [1] 코멘트가 없는 컬럼
-- SELECT table_name, column_name
--   FROM information_schema.columns
--  WHERE table_schema = 'anonymized'
--    AND col_description(('anonymized.' || table_name)::regclass, ordinal_position) IS NULL;
--
-- [2] 익명처리 방법을 노출하는 표현이 코멘트에 남아 있는지
-- SELECT c.relname, d.objsubid, d.description
--   FROM pg_description d
--   JOIN pg_class c     ON c.oid = d.objoid
--   JOIN pg_namespace n ON n.oid = c.relnamespace
--  WHERE n.nspname = 'anonymized'
--    AND d.description ~ '(k-익명|k=|억제|일반화|토큰|salt|해시|손실률|mart)';
-- =====================================================================
