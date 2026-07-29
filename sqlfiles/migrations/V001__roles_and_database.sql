-- =====================================================================
-- [V001] 역할·데이터베이스 기본 설정 — 최초 1회, superuser로 실행
--
-- 이 파일이 하는 일:
--   1) 타임존 UTC 고정
--   2) portfolio_admin에게 스키마 생성 권한 부여 (V002~ 실행에 필요)
--   3) 스키마 3개(mart/anonymized/service)를 portfolio_admin 소유로 생성
--   4) 앞으로 생성될 테이블의 기본 권한(DEFAULT PRIVILEGES) 설정
--
-- 사전 작업 (셸에서):
--   createdb -U <superuser> portfolio
--
-- 사전 작업 (역할 3개 생성 — 비밀번호가 들어가므로 파일에 넣지 않음):
--   psql -d portfolio -c "CREATE ROLE agent_svc      LOGIN PASSWORD '<직접입력>';"
--   psql -d portfolio -c "CREATE ROLE app_svc        LOGIN PASSWORD '<직접입력>';"
--   psql -d portfolio -c "CREATE ROLE portfolio_admin LOGIN PASSWORD '<직접입력>';"
--
--   agent_svc      : LLM/AI 에이전트용. anonymized 전체 SELECT + service 실행계층 최소권한.
--   app_svc        : 담당자 대면 서비스용. service 스키마 CRUD. mart 접근 불가.
--   portfolio_admin : 스키마 소유자. 마이그레이션(psql·Alembic)·배치 전용,
--                    앱 런타임에는 사용하지 않음. 개인 OS 계정을 대체한다.
--
-- 실행: psql -v ON_ERROR_STOP=1 -d portfolio -f V001__roles_and_database.sql
-- 다음: V002 (이후는 portfolio_admin 계정으로 실행할 것 — README 참고)
-- =====================================================================

BEGIN;

\if :{?DB_NAME}
\else
\set DB_NAME 'portfolio'
\endif

-- ---------------------------------------------------------------------
-- 1. 타임존 고정
--
-- transaction_datetime 등 TIMESTAMPTZ 값은 UTC로 저장되지만 세션 타임존에 따라
-- 표시가 달라진다. 팀원 로컬 환경마다 기본값이 다르면 EXTRACT(HOUR) 결과가
-- 흔들려 "이상거래는 새벽 3시" 같은 분석이 환경마다 달라진다 — DB 레벨에 고정.
-- (적용은 새 세션부터. 이 파일 실행 후 재접속할 것)
-- ---------------------------------------------------------------------
ALTER DATABASE :"DB_NAME" SET timezone TO 'UTC';

-- ---------------------------------------------------------------------
-- 2. portfolio_admin에게 스키마 생성 권한
--
-- 왜 필요한가: V002~V005가 CREATE SCHEMA mart/anonymized 을 수행하는데, PostgreSQL에서
-- 스키마 생성은 데이터베이스에 대한 CREATE 권한을 요구한다. 기본적으로 이 권한은
-- DB 소유자에게만 있으므로, portfolio_admin으로 마이그레이션을 실행하려면 명시적
-- 부여가 필요하다.
--
-- 왜 portfolio_admin으로 실행해야 하는가: PostgreSQL은 CREATE TABLE을 "실행한
-- 계정"을 자동으로 소유자로 삼는다. 개인 계정으로 실행하면 그 사람만 이후
-- ALTER/TRUNCATE/트리거제어를 할 수 있게 되어, 팀원·배포 환경마다 소유자가
-- 달라지고 Alembic·배치가 실패한다. 소유자를 portfolio_admin 하나로 고정한다.
-- ---------------------------------------------------------------------
GRANT CREATE, CONNECT ON DATABASE :"DB_NAME" TO portfolio_admin;

-- ---------------------------------------------------------------------
-- 3. 스키마 3개 생성 (소유자 = portfolio_admin)
--
-- 왜 여기서 만드는가:
--   - service 스키마를 만드는 주체가 어디에도 없었다. Alembic의 baseline은
--     op.create_table()만 수행하고 CREATE SCHEMA를 하지 않으며, env.py도
--     스키마를 만들지 않는다. 그 결과 신규 환경에서 `alembic upgrade head`가
--     "schema service does not exist"로 실패한다 — 여기서 미리 만들어 해결한다.
--   - mart/anonymized은 V002/V005에도 CREATE SCHEMA IF NOT EXISTS가 있지만,
--     소유자를 한곳에서 명시적으로 고정하기 위해 여기서 함께 만든다
--     (V002/V005의 구문은 이미 존재하므로 무해하게 건너뛴다).
--
-- AUTHORIZATION을 쓰는 이유: 이 파일은 superuser로 실행되는데, 그냥 만들면
--   superuser가 소유자가 된다. 소유자를 portfolio_admin으로 고정해야
--   이후 마이그레이션·배치가 개인/슈퍼 계정 없이 동작한다.
-- ---------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS mart    AUTHORIZATION portfolio_admin;
CREATE SCHEMA IF NOT EXISTS anonymized    AUTHORIZATION portfolio_admin;
CREATE SCHEMA IF NOT EXISTS service AUTHORIZATION portfolio_admin;

-- ---------------------------------------------------------------------
-- 4. 기본 권한 (DEFAULT PRIVILEGES)
--
-- 앞으로 portfolio_admin이 만드는 service 테이블에 app_svc 권한을 자동 부여한다.
-- Alembic 마이그레이션으로 새 테이블이 추가될 때마다 수동 GRANT를 하지 않아도
-- 되게 하는 장치 — 빠뜨리면 신규 기능에서 permission denied가 발생한다.
--
-- 주의: DEFAULT PRIVILEGES는 "누가 만든 객체인가"에 따라 적용된다. 그래서
-- FOR ROLE portfolio_admin 을 명시한다(Alembic이 이 계정으로 접속하므로).
-- ---------------------------------------------------------------------
ALTER DEFAULT PRIVILEGES FOR ROLE portfolio_admin IN SCHEMA service
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_svc;

ALTER DEFAULT PRIVILEGES FOR ROLE portfolio_admin IN SCHEMA service
    GRANT USAGE, SELECT ON SEQUENCES TO app_svc;

-- anonymized 계층은 agent_svc가 읽기만 한다(LLM이 보는 유일한 데이터 계층).
ALTER DEFAULT PRIVILEGES FOR ROLE portfolio_admin IN SCHEMA anonymized
    GRANT SELECT ON TABLES TO agent_svc;

COMMIT;

-- =====================================================================
-- 검증 (실행 후 확인)
-- =====================================================================
-- SHOW timezone;   -- 재접속 후 UTC 여야 함
--
-- SELECT has_database_privilege('portfolio_admin','portfolio','CREATE');  -- t
--
-- -- 스키마 3개가 portfolio_admin 소유로 존재하는가 (3행이어야 정상)
-- SELECT nspname, pg_get_userbyid(nspowner) FROM pg_namespace
--  WHERE nspname IN ('mart','anonymized','service');
--
-- SELECT pg_get_userbyid(defaclrole) AS 역할, n.nspname AS 스키마,
--        defaclobjtype::text AS 종류, defaclacl::text AS 권한
--   FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace;
