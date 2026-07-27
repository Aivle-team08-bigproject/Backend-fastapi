-- =====================================================================
-- [P001] 기존 DB 보정 — 이미 구축된 portfolio를 새 표준에 맞춘다
--
-- 대상: 2026-07 이전에 개인 계정(dowon)으로 구축된 로컬 DB.
--       **새로 구축하는 환경은 이 파일이 필요 없다** (V001~V006이 처음부터
--       올바른 소유자로 만들기 때문). 신규 팀원은 실행하지 말 것.
--
-- 배경: PostgreSQL은 CREATE TABLE을 실행한 계정을 소유자로 삼는다. 초기 구축을
--   개인 OS 계정으로 진행해서 mart/anon/service의 오브젝트 소유자가 개인 계정이
--   되었고, 그 결과 portfolio_admin(마이그레이션·배치 전용 계정)이
--     - ALTER TABLE (Alembic 마이그레이션)
--     - TRUNCATE / DISABLE TRIGGER (익명화 배치)
--   를 수행할 수 없다. 이 둘은 GRANT로 줄 수 없고 "소유자"만 가능하다.
--
-- 이 파일이 하는 일: mart/anon/service의 모든 테이블·함수 소유권을
--   portfolio_admin으로 이전한다. (V14__ownership_transfer.sql을 흡수·확장한 것 —
--   V14는 mart/anon만 다뤄서 service 21개 테이블이 남아 있었다.)
--
-- 안전성:
--   - 소유권 변경은 기존 GRANT를 지우지 않는다(agent_svc/app_svc 권한 유지).
--   - 데이터는 건드리지 않는다.
--   - superuser는 이전 후에도 모든 작업이 가능하다.
--
-- 실행: psql -v ON_ERROR_STOP=1 -d portfolio -f P001__align_existing_db.sql
--       (superuser 계정으로 실행할 것)
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1. 테이블 소유권 이전 (mart / anon / service 전부)
--
-- 이름을 일일이 나열하지 않고 동적으로 처리한다 — 테이블이 추가·변경돼도
-- 이 파일을 고칠 필요가 없고, 이미 portfolio_admin 소유인 것은 건너뛴다.
-- ---------------------------------------------------------------------
DO $$
DECLARE
    r record;
    n int := 0;
BEGIN
    FOR r IN
        SELECT schemaname, tablename
          FROM pg_tables
         WHERE schemaname IN ('mart', 'anon', 'service')
           AND tableowner <> 'portfolio_admin'
    LOOP
        EXECUTE format('ALTER TABLE %I.%I OWNER TO portfolio_admin',
                       r.schemaname, r.tablename);
        n := n + 1;
    END LOOP;
    RAISE NOTICE '[P001] 테이블 소유권 이전: %건', n;
END $$;

-- ---------------------------------------------------------------------
-- 2. 함수 소유권 이전 (정합성 트리거 함수 등)
--
-- 테이블만 넘기면 배치·마이그레이션은 동작하지만, 함수 소유자가 개인 계정으로
-- 남으면 나중에 트리거 로직을 수정·삭제할 때 다시 그 계정이 필요해진다.
-- ---------------------------------------------------------------------
DO $$
DECLARE
    r record;
    n int := 0;
BEGIN
    FOR r IN
        SELECT n.nspname AS schema_name,
               p.oid::regprocedure AS func_sig
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname IN ('mart', 'anon', 'service')
           AND pg_get_userbyid(p.proowner) <> 'portfolio_admin'
    LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO portfolio_admin', r.func_sig);
        n := n + 1;
    END LOOP;
    RAISE NOTICE '[P001] 함수 소유권 이전: %건', n;
END $$;

-- ---------------------------------------------------------------------
-- 3. 스키마 소유권 (이미 되어 있으면 무해하게 재적용)
-- ---------------------------------------------------------------------
ALTER SCHEMA mart    OWNER TO portfolio_admin;
ALTER SCHEMA anon    OWNER TO portfolio_admin;
ALTER SCHEMA service OWNER TO portfolio_admin;

-- ---------------------------------------------------------------------
-- 4. V001의 설정을 기존 DB에도 적용 (누락분 보정)
-- ---------------------------------------------------------------------
GRANT CREATE, CONNECT ON DATABASE portfolio TO portfolio_admin;

ALTER DEFAULT PRIVILEGES FOR ROLE portfolio_admin IN SCHEMA service
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_svc;
ALTER DEFAULT PRIVILEGES FOR ROLE portfolio_admin IN SCHEMA service
    GRANT USAGE, SELECT ON SEQUENCES TO app_svc;
ALTER DEFAULT PRIVILEGES FOR ROLE portfolio_admin IN SCHEMA anon
    GRANT SELECT ON TABLES TO agent_svc;

COMMIT;

-- =====================================================================
-- 검증 (실행 후 확인 — 1·2는 0행이어야 정상)
-- =====================================================================
-- 1) 소유자가 portfolio_admin이 아닌 테이블
-- SELECT schemaname, tablename, tableowner FROM pg_tables
--  WHERE schemaname IN ('mart','anon','service') AND tableowner <> 'portfolio_admin';
--
-- 2) 소유자가 portfolio_admin이 아닌 함수
-- SELECT n.nspname, p.proname, pg_get_userbyid(p.proowner)
--   FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
--  WHERE n.nspname IN ('mart','anon','service')
--    AND pg_get_userbyid(p.proowner) <> 'portfolio_admin';
--
-- 3) 기존 GRANT 유지 확인 — app_svc는 service 21개, agent_svc는 anon 5개 + service 일부
-- SELECT grantee, table_schema, count(DISTINCT table_name)
--   FROM information_schema.role_table_grants
--  WHERE grantee IN ('agent_svc','app_svc') GROUP BY 1,2 ORDER BY 1,2;
--
-- 4) 보안 경계 유지 — agent_svc는 mart에 접근 불가여야 함
-- SET ROLE agent_svc; SELECT count(*) FROM mart.customers;  -- 에러가 나야 정상
-- RESET ROLE;
