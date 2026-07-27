-- =====================================================================
-- [verify] mart 데이터 검증 리포트 — 조회 전용, 언제든 재실행 가능
--
-- 이 파일은 SELECT만 수행하며 DB를 변경하지 않는다. 마이그레이션 체인의 일부가
-- 아니므로 migrations/ 가 아니라 verify/ 에 둔다.
--
-- 실행 시점: seed/load_csv.sql(적재) 완료 후
-- 실행: psql -U portfolio_admin -d portfolio -f verify/validation_report.sql
--
-- 목적: 초기 설계에서 발견됐던 결함이 해소되었는지, 보존하기로 한 패턴이
--       살아있는지를 데이터로 확인한다. 모든 항목이 기대값과 일치해야 한다.
-- =====================================================================

\echo '=========================================================='
\echo ' [1] 과거 데이터에서 트리거·CHECK에 걸렸던 결함'
\echo '     전부 0이어야 함 (초기 데이터셋에서는 810건이 거부되어 적재 0건이었음)'
\echo '=========================================================='
SELECT '카드 발급 전 거래' AS "검증 항목", count(*) AS "위반", '0' AS "기대값"
FROM mart.transactions t JOIN mart.cards c USING (card_number_masked)
WHERE to_char(t.transaction_datetime, 'YYYY-MM') < c.card_issue_month

UNION ALL SELECT '해지 카드의 해지 후 거래', count(*), '0'
FROM mart.transactions t JOIN mart.cards c USING (card_number_masked)
WHERE c.card_status = '해지'
  AND to_char(t.transaction_datetime, 'YYYY-MM') > c.card_status_changed_month

UNION ALL SELECT '가맹점 개업 전 거래', count(*), '0'
FROM mart.transactions t JOIN mart.merchants m USING (merchant_id)
WHERE to_char(t.transaction_datetime, 'YYYY-MM') < m.merchant_open_month

UNION ALL SELECT '폐업 가맹점의 폐업 후 거래', count(*), '0'
FROM mart.transactions t JOIN mart.merchants m USING (merchant_id)
WHERE m.merchant_status = '폐업'
  AND to_char(t.transaction_datetime, 'YYYY-MM') > m.merchant_status_changed_month

UNION ALL SELECT '국내거래 MCC 불일치', count(*), '0'
FROM mart.transactions t JOIN mart.merchants m USING (merchant_id)
WHERE t.mcc_code <> m.mcc_code

UNION ALL SELECT '체크카드 할부', count(*), '0'
FROM mart.transactions t JOIN mart.cards c USING (card_number_masked)
WHERE c.card_product_code LIKE 'CK%' AND t.installment_months > 0

UNION ALL SELECT '0원 이하 거래', count(*), '0'
FROM mart.transactions WHERE krw_converted_amount <= 0;

\echo ''
\echo '=========================================================='
\echo ' [2] DB 제약으로는 잡을 수 없어 생성 단계에서 해결한 결함'
\echo '     (업무 규칙이므로 CHECK/트리거의 책임 범위가 아님)'
\echo '=========================================================='
SELECT '10대인데 공무원/전문직/사무직' AS "검증 항목", count(*) AS "위반", '0' AS "기대값"
FROM mart.customers
WHERE age_band = '10대' AND occupation IN ('공무원', '전문직', '사무직', '자영업')

UNION ALL SELECT '학생/무직인데 고한도(3천만 이상)', count(*), '0'
FROM mart.cards c JOIN mart.customers cu USING (customer_id)
WHERE cu.occupation IN ('학생', '무직/은퇴') AND c.credit_limit_band = '3,000만원 이상'

UNION ALL SELECT '우편번호-거주지역 불일치', count(*), '0'
FROM mart.customers
WHERE NOT (
     (resident_region = '서울특별시 강남구'   AND left(postal_code,3) BETWEEN '060' AND '064')
  OR (resident_region = '서울특별시 강서구'   AND left(postal_code,3) BETWEEN '075' AND '078')
  OR (resident_region = '서울특별시 관악구'   AND left(postal_code,3) BETWEEN '087' AND '089')
  OR (resident_region = '서울특별시 광진구'   AND left(postal_code,3) BETWEEN '049' AND '051')
  OR (resident_region = '서울특별시 구로구'   AND left(postal_code,3) BETWEEN '082' AND '084')
  OR (resident_region = '서울특별시 노원구'   AND left(postal_code,3) BETWEEN '016' AND '019')
  OR (resident_region = '서울특별시 동작구'   AND left(postal_code,3) BETWEEN '069' AND '071')
  OR (resident_region = '서울특별시 마포구'   AND left(postal_code,3) BETWEEN '039' AND '042')
  OR (resident_region = '서울특별시 서초구'   AND left(postal_code,3) BETWEEN '065' AND '068')
  OR (resident_region = '서울특별시 성동구'   AND left(postal_code,3) BETWEEN '047' AND '048')
  OR (resident_region = '서울특별시 송파구'   AND left(postal_code,3) BETWEEN '055' AND '059')
  OR (resident_region = '서울특별시 영등포구' AND left(postal_code,3) BETWEEN '072' AND '074')
  OR (resident_region = '서울특별시 용산구'   AND left(postal_code,3) BETWEEN '043' AND '044')
  OR (resident_region = '서울특별시 은평구'   AND left(postal_code,3) BETWEEN '033' AND '035')
  OR (resident_region = '서울특별시 종로구'   AND left(postal_code,3) BETWEEN '030' AND '032')
);

\echo ''
\echo '=========================================================='
\echo ' [3] 통화별 금액 스케일'
\echo '     과거 결함: 모든 외화의 평균 거래금액이 160 내외로 동일하여'
\echo '                  베트남 9원, 미국 23만원처럼 국가별 소비가 비현실적이었음'
\echo '     기대: 통화별로 평균 현지금액은 크게 다르고, 평균 원화환산액은 유사한 수준'
\echo '=========================================================='
SELECT currency_code                      AS "통화",
       count(*)                           AS "건수",
       round(avg(transaction_amount))     AS "평균 현지금액",
       round(avg(krw_converted_amount))   AS "평균 원화환산",
       min(transaction_amount)            AS "최소 현지금액"
FROM mart.transactions
GROUP BY 1 ORDER BY 3;

\echo ''
\echo '=========================================================='
\echo ' [4] 보존한 패턴 — 부정사용 탐지용 이상거래'
\echo '     기대: FDS 차단 건은 정상 건 대비 심야·고액·무인증 비율이 뚜렷이 높음'
\echo '=========================================================='
SELECT CASE WHEN decline_reason_code = '자체FDS의심차단' THEN 'FDS 차단'
            WHEN approval_status = '승인' THEN '정상 승인'
            ELSE '기타 거절' END                                    AS "구분",
       count(*)                                                     AS "건수",
       round(avg(extract(hour FROM transaction_datetime)))          AS "평균 시각",
       round(avg(krw_converted_amount))                             AS "평균 금액",
       round(100.0 * count(*) FILTER (WHERE auth_method = '없음') / count(*), 1) AS "무인증 %"
FROM mart.transactions
GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '=========================================================='
\echo ' [5] 보존한 패턴 — 해외 여행'
\echo '     기대: 여행객이 특정 국가에 며칠간 체류하며 집중 결제'
\echo '=========================================================='
SELECT count(DISTINCT card_number_masked)                    AS "해외거래 카드 수",
       round(avg(days), 1)                                   AS "평균 체류일",
       round(avg(cnt), 1)                                    AS "여행당 평균 결제건수"
FROM (
    SELECT card_number_masked, merchant_country_code, count(*) AS cnt,
           max(transaction_datetime)::date - min(transaction_datetime)::date AS days
    FROM mart.transactions
    WHERE merchant_country_code <> 'KR'
    GROUP BY 1, 2
) x;

\echo ''
\echo '=========================================================='
\echo ' [6] 개선 — 단말기 밀도'
\echo '     과거 결함: 거래 12,477건에 단말기 12,397종으로 거래마다 단말기가 달라'
\echo '                  "동일 단말기에서 여러 카드 사용"이라는 복제 패턴이 성립 불가'
\echo '=========================================================='
SELECT count(DISTINCT terminal_id)                                   AS "단말기 종류",
       round(count(*)::numeric / count(DISTINCT terminal_id), 1)     AS "단말기당 거래수",
       (SELECT max(c) FROM (SELECT count(DISTINCT card_number_masked) c
                            FROM mart.transactions GROUP BY terminal_id) y) AS "단말기당 최대 카드수"
FROM mart.transactions;

\echo ''
\echo '=========================================================='
\echo ' [7] 해외 전용 업종의 업종명 조회'
\echo '     과거 결함: 국내 가맹점이 없는 업종은 merchants에 없어 업종명을 알 수 없었음'
\echo '     기대: mcc_codes 분리로 해외거래도 업종명 조회 가능'
\echo '=========================================================='
SELECT mc.mcc_code       AS "업종코드",
       mc.mcc_name       AS "업종명",
       count(t.transaction_id) AS "거래수",
       count(*) FILTER (WHERE t.merchant_id IS NULL) AS "해외거래",
       count(*) FILTER (WHERE t.merchant_id IS NOT NULL) AS "국내거래"
FROM mart.mcc_codes mc LEFT JOIN mart.transactions t USING (mcc_code)
GROUP BY 1, 2 ORDER BY 3 DESC;

\echo ''
\echo '=========================================================='
\echo ' [8] 미해결 — k-익명성 (anon 계층에서 처리 예정)'
\echo '     준식별자 조합에 1명뿐인 경우가 존재하여 단독 식별 가능.'
\echo '     원인은 고객 800명 대비 조합 수가 많은 것으로, 규모가 커지면 자연 해소됨.'
\echo '     가명정보는 접근통제를 전제하므로 내부 이용 목적상 허용 범위이나,'
\echo '     외부 제공용 익명정보 생성 시에는 반드시 해소해야 함.'
\echo '=========================================================='
SELECT count(*)                        AS "준식별자 조합 수",
       min(n)                          AS "최소 그룹 크기",
       count(*) FILTER (WHERE n = 1)   AS "1명뿐인 조합",
       count(*) FILTER (WHERE n >= 5)  AS "k>=5 충족 조합"
FROM (SELECT count(*) AS n FROM mart.customers
      GROUP BY gender, age_band, resident_region) g;

\echo ''
\echo '=========================================================='
\echo ' [9] 요구사항별 데이터 가용성'
\echo '=========================================================='
SELECT '지역x시간대 밀집도 (정부기관)' AS "요구사항",
       (SELECT count(*) FROM (SELECT m.merchant_region, extract(hour FROM t.transaction_datetime)
                              FROM mart.transactions t JOIN mart.merchants m USING (merchant_id)
                              GROUP BY 1,2) a)::text || ' 조합' AS "가용 데이터"
UNION ALL SELECT '연령대x업종 교차 (학원 마케팅)',
       (SELECT count(*) FROM (SELECT c.age_band, t.mcc_code
                              FROM mart.transactions t
                              JOIN mart.cards ca USING (card_number_masked)
                              JOIN mart.customers c USING (customer_id)
                              GROUP BY 1,2) b)::text || ' 조합'
UNION ALL SELECT '해외 소비 패턴 (하나투어)',
       (SELECT count(*) FROM mart.transactions WHERE merchant_country_code <> 'KR')::text || ' 건'
UNION ALL SELECT '이상거래 라벨 (FDS 연구)',
       (SELECT count(*) FROM mart.transactions
        WHERE decline_reason_code = '자체FDS의심차단')::text || ' 건';
