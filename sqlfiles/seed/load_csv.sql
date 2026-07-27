-- =====================================================================
-- [seed] mart 데이터 적재 — 마이그레이션이 아닌 선택 단계
--
-- 이 파일은 스키마를 바꾸지 않는다. 구조만 필요한 팀원은 실행하지 않아도 된다.
-- 그래서 migrations/ 가 아니라 seed/ 에 둔다.
--
-- 실행 시점: migrations/V004(트리거) 완료 후
-- 실행 방법:
--     cd seed && psql -v ON_ERROR_STOP=1 -U hanacard_admin -d hanacard -f load_csv.sql
--
--   ⚠️ 반드시 seed/ 디렉터리 "안에서" 실행할 것.
--      \copy의 상대경로는 SQL 파일 위치가 아니라 psql을 실행한 디렉터리를 기준으로
--      해석된다. CSV 5개를 이 디렉터리에 함께 두는 이유다.
--
--   ※ \copy는 psql 클라이언트 명령이므로 DBeaver SQL Editor에서는 동작하지 않는다.
--     DBeaver를 쓸 경우 각 테이블 우클릭 → Import Data 사용.
--
-- 적재 순서는 FK 의존성을 따라야 한다:
--   mcc_codes → customers → cards → merchants → transactions
--
-- 기대 결과: 5개 테이블 모두 적재 성공.
--   이 CSV는 컬럼 간 의존관계를 반영해 생성된 것이라 모든 CHECK 제약과
--   정합성 트리거를 통과한다(적재 중 거부되는 행이 없어야 정상).
-- =====================================================================

\echo '--- 1/5 mcc_codes ---'
\copy mart.mcc_codes FROM 'mcc_codes.csv' WITH (FORMAT csv, HEADER true, NULL '');

\echo '--- 2/5 customers ---'
\copy mart.customers FROM 'customers.csv' WITH (FORMAT csv, HEADER true, NULL '');

\echo '--- 3/5 cards ---'
\copy mart.cards FROM 'cards.csv' WITH (FORMAT csv, HEADER true, NULL '');

\echo '--- 4/5 merchants ---'
\copy mart.merchants FROM 'merchants.csv' WITH (FORMAT csv, HEADER true, NULL '');

\echo '--- 5/5 transactions (트리거 5종이 행마다 실행됨) ---'
\copy mart.transactions FROM 'transactions.csv' WITH (FORMAT csv, HEADER true, NULL '');

-- ---------------------------------------------------------------------
-- 적재 결과
-- ---------------------------------------------------------------------
\echo ''
\echo '=== 적재 결과 ==='
SELECT 'mcc_codes'    AS "테이블", count(*) AS "행수" FROM mart.mcc_codes
UNION ALL SELECT 'customers',    count(*) FROM mart.customers
UNION ALL SELECT 'cards',        count(*) FROM mart.cards
UNION ALL SELECT 'merchants',    count(*) FROM mart.merchants
UNION ALL SELECT 'transactions', count(*) FROM mart.transactions;

-- 통계 정보 갱신 — 옵티마이저가 올바른 실행 계획을 세우도록
ANALYZE mart.mcc_codes;
ANALYZE mart.customers;
ANALYZE mart.cards;
ANALYZE mart.merchants;
ANALYZE mart.transactions;
