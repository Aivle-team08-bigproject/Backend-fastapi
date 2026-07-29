-- =====================================================================
-- [V010] agent_svc 권한 축소 — 테이블 단위 → 컬럼 단위
-- 대상: PostgreSQL 17, portfolio DB
--
-- 실행: psql -v ON_ERROR_STOP=1 -U portfolio_admin -d portfolio \
--             -f migrations/V010__agent_svc_column_grants.sql
-- 이전: V009 (automation 스키마)
--
-- ---------------------------------------------------------------------
-- 왜 이 파일이 필요한가
-- ---------------------------------------------------------------------
-- V006은 에이전트가 "처리 컨텍스트"를 읽을 수 있도록 아래 권한을 줬다.
--
--     GRANT SELECT ON service.data_requests, service.clients TO agent_svc;
--
-- 의도는 요구사항 원문·고객사명을 읽게 하는 것이었으나, 테이블 단위라
-- 실제 연락처까지 함께 열렸다.
--
--     service.data_requests.sample_email   실제 이메일 주소
--     service.clients.contact_email        실제 이메일 주소
--
-- 카드 데이터는 k-익명성까지 맞춰 익명화해 두고, 정작 담당자 연락처는
-- 에이전트 경로에 그대로 노출돼 있던 셈이다.
-- V006 헤더가 선언한 경계("employees·contracts·reviews·deliveries 접근 불가")의
-- 취지에도 어긋난다.
--
-- PostgreSQL은 컬럼 단위 GRANT를 지원하므로, 필요한 컬럼만 남긴다.
--
-- ---------------------------------------------------------------------
-- V006을 고치지 않고 새 파일로 만드는 이유
-- ---------------------------------------------------------------------
-- 이미 팀원 로컬에 적용된 마이그레이션을 수정하면, 적용한 사람과 안 한 사람의
-- DB 상태가 갈린다. 마이그레이션은 덮어쓰지 않고 이어 붙인다.
-- 신규 환경은 V006 -> V010 순으로 적용되어 결과가 같아진다.
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1. 기존 테이블 단위 권한 회수
-- ---------------------------------------------------------------------
-- 컬럼 단위 GRANT는 테이블 단위 GRANT를 덮어쓰지 않는다(둘은 합집합으로 동작).
-- 반드시 먼저 회수해야 실제로 좁혀진다.
REVOKE SELECT ON service.data_requests FROM agent_svc;
REVOKE SELECT ON service.clients       FROM agent_svc;

-- ---------------------------------------------------------------------
-- 2. 필요한 컬럼만 재부여
-- ---------------------------------------------------------------------
-- data_requests — 요구사항 분석·데이터 선별에 실제로 쓰이는 것만.
--   제외: sample_email (실제 연락처)
--         owner_id     (담당 실무자 식별자. 에이전트 판단에 불필요)
GRANT SELECT (
    id,
    client_id,
    title,
    business_purpose,
    raw_requirement,
    output_formats,
    delivery_channels,
    usage_period,
    analysis_condition,
    created_at,
    updated_at
) ON service.data_requests TO agent_svc;

-- clients — 고객사 맥락은 상호명이면 충분하다.
--   제외: contact_email (실제 연락처)
GRANT SELECT (
    id,
    company_name
) ON service.clients TO agent_svc;

COMMIT;

-- ---------------------------------------------------------------------
-- 3. 컬럼 코멘트 — 이 컬럼들이 왜 제외됐는지 남긴다
-- ---------------------------------------------------------------------
-- service 코멘트는 사람(담당자·개발자)만 보므로 근거를 적어도 된다.
-- anonymized과 달리 LLM 컨텍스트에 들어가지 않는다.
COMMENT ON COLUMN service.data_requests.sample_email IS
'샘플 전달용 이메일. 실제 연락처이므로 agent_svc에 노출하지 않는다(V010).
에이전트 경로는 익명/가공 데이터만 다룬다는 원칙의 일부.';

COMMENT ON COLUMN service.clients.contact_email IS
'고객사 담당자 이메일. 실제 연락처이므로 agent_svc에 노출하지 않는다(V010).';

-- =====================================================================
-- 4. 검증 (실행 후 확인용)
-- =====================================================================
-- -- 이메일 컬럼이 차단됐는지 (둘 다 f여야 정상)
-- SELECT has_column_privilege('agent_svc','service.data_requests','sample_email','SELECT') AS sample_email,
--        has_column_privilege('agent_svc','service.clients','contact_email','SELECT')      AS contact_email;
--
-- -- 필요한 컬럼은 살아있는지 (둘 다 t여야 정상)
-- SELECT has_column_privilege('agent_svc','service.data_requests','raw_requirement','SELECT') AS raw_requirement,
--        has_column_privilege('agent_svc','service.clients','company_name','SELECT')          AS company_name;
--
-- -- 테이블 단위 권한이 남아있지 않은지 (0줄이어야 정상)
-- SELECT table_name FROM information_schema.table_privileges
--  WHERE grantee='agent_svc' AND table_schema='service'
--    AND table_name IN ('data_requests','clients');
--
-- -- 실제 부여된 컬럼 목록
-- SELECT table_name, column_name FROM information_schema.column_privileges
--  WHERE grantee='agent_svc' AND table_schema='service' ORDER BY 1, 2;

-- =====================================================================
-- 5. 주의 — SELECT * 는 실패한다
-- =====================================================================
-- 컬럼 단위 권한에서는 `SELECT *`가 권한 없는 컬럼까지 요구하므로 거부된다.
-- agent_svc로 이 두 테이블을 조회하는 코드는 컬럼을 명시해야 한다.
-- (SQLAlchemy ORM이 모델 전체 컬럼을 SELECT하므로, 해당 모델을 agent 세션으로
--  조회하는 코드가 생기면 load_only() 등으로 컬럼을 제한할 것)
