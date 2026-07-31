-- =====================================================================
-- [V006] service 스키마 — GRANT (agent_svc/app_svc, 테이블 단위)
--
-- ⚠️ 실행 시점: **`alembic upgrade head` 다음**.
--   service 테이블은 Alembic이 만들므로(레포 alembic/), 테이블이 존재해야
--   GRANT ... ON ALL TABLES가 의미를 갖는다. 순서를 지키지 않으면 아무것도
--   부여되지 않은 채 조용히 성공한다.
--
-- 실행: psql -v ON_ERROR_STOP=1 -U portfolio_admin -d portfolio \
--             -f migrations/V006__service_grants.sql
--
-- 전제: anon GRANT(V005)는 이미 적용됨. mart는 앱 계정에 부여하지 않음(배치/DBA만).
-- 참고: PK가 GENERATED ... AS IDENTITY라 시퀀스 별도 GRANT 불필요
--       (IDENTITY 시퀀스는 테이블 권한에 포함되어 처리됨).
-- 참고: 앞으로 Alembic이 추가하는 테이블은 V001의 DEFAULT PRIVILEGES로 app_svc에
--       자동 부여된다. 다만 agent_svc는 테이블별 최소권한이라 자동화 대상이 아니므로,
--       실행계층 테이블이 새로 생기면 이 파일에 수동으로 추가해야 한다.
-- =====================================================================

-- ---------------------------------------------------------------------
-- app_svc — 웹/대시보드. service 전체 CRUD.
-- ---------------------------------------------------------------------
GRANT USAGE ON SCHEMA service TO app_svc;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA service TO app_svc;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA service TO app_svc;
ALTER DEFAULT PRIVILEGES IN SCHEMA service
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_svc;
ALTER DEFAULT PRIVILEGES IN SCHEMA service
    GRANT USAGE, SELECT ON SEQUENCES TO app_svc;

-- ---------------------------------------------------------------------
-- agent_svc — 워커/에이전트 실행. anon 읽기(V6) + service 최소 권한.
--   읽기: 처리 컨텍스트 / 쓰기: 실행상태·로그·산출물
--   못 하는 것(의도): employees·login_sessions·admin_audit_logs·contracts·
--                     contract_api_keys·reviews·deliveries·api_usage_logs·mart 접근 불가
-- ---------------------------------------------------------------------
GRANT USAGE ON SCHEMA service TO agent_svc;
GRANT SELECT ON service.data_requests, service.clients TO agent_svc;
GRANT SELECT, INSERT, UPDATE ON service.pipeline_runs, service.stage_runs TO agent_svc;
GRANT SELECT, INSERT ON service.pipeline_events, service.agent_metrics, service.artifacts TO agent_svc;

-- ---------------------------------------------------------------------
-- PUBLIC 회수 (기본 노출 차단)
-- ---------------------------------------------------------------------
REVOKE ALL ON SCHEMA service FROM PUBLIC;

-- ---------------------------------------------------------------------
-- 검증용 (참고, 실행 후 확인)
-- ---------------------------------------------------------------------
-- SELECT grantee, count(*) FROM information_schema.role_table_grants
--   WHERE table_schema='service' AND grantee IN ('agent_svc','app_svc') GROUP BY grantee;
-- -- app_svc ~88, agent_svc ~14 기대
--
-- SELECT table_name FROM information_schema.role_table_grants
--   WHERE grantee='agent_svc' AND table_schema='service'
--     AND table_name IN ('employees','contracts','reviews','deliveries','login_sessions');
-- -- 0줄이어야 정상(경계 유지)
