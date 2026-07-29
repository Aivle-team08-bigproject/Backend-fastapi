-- =====================================================================
-- [V011] automation 스키마 — 상태·단계 CHECK 완화
-- 대상: PostgreSQL 17, portfolio DB
--
-- 실행: psql -v ON_ERROR_STOP=1 -U portfolio_admin -d portfolio \
--             -f migrations/V011__automation_relax_status_checks.sql
-- 이전: V010 (agent_svc 컬럼 단위 권한)
--
-- ---------------------------------------------------------------------
-- 왜 이 파일이 필요한가
-- ---------------------------------------------------------------------
-- V009는 상태·단계 값을 CHECK 제약에 그대로 열거했다.
--
--     CHECK (status IN ('QUEUED','RUNNING','WAITING_HITL',
--                       'COMPLETED','FAILED','WAITING_RETRY'))
--
-- 그런데 이 값들은 애플리케이션 enum(automation-supervisor-api/app/domain/enums.py)에서
-- 오고, 파이프라인 설계가 진행되면서 계속 늘어난다. 실제로 2026-07-29에
-- 아래 값들이 추가되면서 V009를 적용한 DB에서는 INSERT/UPDATE가 거부됐다.
--
--     JobStatus  + SUPERVISOR_QUEUED, WORKER_CREATED, REJECTED
--     StageName  + DATA_RETRIEVAL
--
-- 값 목록을 DB에 고정하면 **코드가 마이그레이션보다 빨리 움직일 때마다 팀 작업이 막힌다.**
-- 값이 하나 늘 때마다 마이그레이션을 새로 쓰는 것은 이 단계의 프로젝트에 맞지 않는다.
--
-- ---------------------------------------------------------------------
-- 대신 무엇으로 지키는가
-- ---------------------------------------------------------------------
-- 1) 형식 검사만 남긴다 — 대문자·숫자·밑줄 조합. 쓰레기 값('', 'abc 123', NULL 문자열)은 막힌다.
-- 2) 값의 정본은 애플리케이션 enum이다. 앱이 유일한 writer이고 enum 상수로만 쓰므로
--    오타로 잘못된 값이 들어갈 경로가 없다.
-- 3) 알려진 값은 COMMENT로 남긴다 — 사람이 조회할 때 참고용이며 제약이 아니다.
--
-- 의미 제약(진행률 범위, 시각 순서)은 값 목록과 무관하므로 그대로 둔다.
--
-- ---------------------------------------------------------------------
-- 함께 완화하는 것 두 가지
-- ---------------------------------------------------------------------
-- * qa_iteration <= max_qa_iterations
--     반복 상한 도달을 "초과 직전"에 판정할지 "초과 후"에 판정할지는 앱 로직이 정한다.
--     DB가 상한을 강제하면 앱이 판정 방식을 바꿀 때 또 막힌다. 하한(0)만 남긴다.
--
-- * status='FAILED'이면 error_message NOT NULL
--     의도는 좋았으나, 사유를 못 채운 실패가 발생하면 **실패 기록 자체가 저장되지 않는다.**
--     사유 없는 실패 기록이 남는 편이, 실패를 통째로 잃는 것보다 낫다.
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1. 값을 열거하던 CHECK 제거
-- ---------------------------------------------------------------------
ALTER TABLE automation.automation_jobs
    DROP CONSTRAINT IF EXISTS ck_automation_jobs_status,
    DROP CONSTRAINT IF EXISTS ck_automation_jobs_stage,
    DROP CONSTRAINT IF EXISTS ck_automation_jobs_rollback,
    DROP CONSTRAINT IF EXISTS ck_automation_jobs_qa;

ALTER TABLE automation.automation_stage_runs
    DROP CONSTRAINT IF EXISTS ck_automation_stage_runs_stage,
    DROP CONSTRAINT IF EXISTS ck_automation_stage_runs_status,
    DROP CONSTRAINT IF EXISTS ck_automation_stage_runs_failure;

-- ---------------------------------------------------------------------
-- 2. 형식 검사로 대체
-- ---------------------------------------------------------------------
-- 대문자로 시작하고 대문자·숫자·밑줄만 허용. enum 상수의 형태를 강제한다.
ALTER TABLE automation.automation_jobs
    ADD CONSTRAINT ck_automation_jobs_status_shape
        CHECK (status ~ '^[A-Z][A-Z0-9_]*$'),
    ADD CONSTRAINT ck_automation_jobs_stage_shape
        CHECK (current_stage IS NULL OR current_stage ~ '^[A-Z][A-Z0-9_]*$'),
    ADD CONSTRAINT ck_automation_jobs_rollback_shape
        CHECK (rollback_to_stage IS NULL OR rollback_to_stage ~ '^[A-Z][A-Z0-9_]*$'),
    ADD CONSTRAINT ck_automation_jobs_qa_nonneg
        CHECK (qa_iteration >= 0 AND max_qa_iterations >= 0);

ALTER TABLE automation.automation_stage_runs
    ADD CONSTRAINT ck_automation_stage_runs_stage_shape
        CHECK (stage_name ~ '^[A-Z][A-Z0-9_]*$'),
    ADD CONSTRAINT ck_automation_stage_runs_status_shape
        CHECK (status ~ '^[A-Z][A-Z0-9_]*$');

-- ---------------------------------------------------------------------
-- 3. 알려진 값을 코멘트로 (제약이 아니라 참고용)
-- ---------------------------------------------------------------------
COMMENT ON COLUMN automation.automation_jobs.status IS
'작업 전체 상태. 값의 정본은 애플리케이션 enum(JobStatus)이며 DB는 형식만 검사한다.
2026-07-29 기준 알려진 값:
  QUEUED             생성됨, Supervisor 실행 대기
  SUPERVISOR_QUEUED  Supervisor Worker가 큐에 등록됨
  WORKER_CREATED     Supervisor가 다음 단계 Worker를 생성함
  RUNNING            단계 Worker 실행 중
  WAITING_HITL       결과 저장 완료, 사람 승인 대기 (이 상태에서는 실행 중인 것이 없다)
  WAITING_RETRY      반려 후 재실행 대기
  REJECTED           반려로 종료
  COMPLETED / FAILED 종료 상태
새 값이 추가돼도 이 코멘트만 갱신하면 되고 마이그레이션은 필요 없다.';

COMMENT ON COLUMN automation.automation_jobs.current_stage IS
'현재 단계. 값의 정본은 애플리케이션 enum(StageName)이다.
2026-07-29 기준: REQUIREMENT_ANALYSIS, DATA_SELECTION, DATA_RETRIEVAL,
DATA_PROCESSING, HITL_REVIEW.
DATA_SELECTION은 "무엇을 어떻게 고를지" 계획을 세우고,
DATA_RETRIEVAL은 그 계획을 실제 데이터에 적용하는 결정론적 단계다.';

COMMENT ON COLUMN automation.automation_jobs.qa_iteration IS
'반려 후 재시도 횟수. 상한 판정은 애플리케이션이 한다(max_qa_iterations 참고).
DB는 음수만 막는다 — 판정 방식이 바뀔 때 마이그레이션이 필요해지지 않도록.';

COMMENT ON COLUMN automation.automation_stage_runs.status IS
'단계 실행 상태. 값의 정본은 애플리케이션 enum(StageStatus)이다.
2026-07-29 기준: PENDING, RUNNING, COMPLETED, FAILED, ROLLED_BACK, CACHED.
CACHED = 동일 입력의 이전 결과를 재사용해 agent를 호출하지 않음.';

COMMENT ON COLUMN automation.automation_stage_runs.error_message IS
'실패 사유. FAILED인데 비어 있을 수 있다 — 사유를 못 채우더라도 실패 기록 자체는
남는 편이 낫기 때문에 NOT NULL 제약을 두지 않는다(V011).';

COMMIT;

-- =====================================================================
-- 4. 검증 (실행 후 확인용)
-- =====================================================================
-- -- 값을 열거하던 제약이 사라졌는지 (0줄이어야 정상)
-- SELECT conname FROM pg_constraint
--  WHERE connamespace = 'automation'::regnamespace
--    AND conname IN ('ck_automation_jobs_status','ck_automation_jobs_stage',
--                    'ck_automation_jobs_rollback','ck_automation_jobs_qa',
--                    'ck_automation_stage_runs_stage','ck_automation_stage_runs_status',
--                    'ck_automation_stage_runs_failure');
--
-- -- 새 상태값이 들어가는지 (rollback 하므로 데이터는 남지 않는다)
-- BEGIN;
-- INSERT INTO automation.automation_jobs (raw_requirement, status, current_stage)
-- VALUES ('제약 확인용', 'SUPERVISOR_QUEUED', 'DATA_RETRIEVAL');
-- ROLLBACK;
--
-- -- 쓰레기 값은 여전히 막히는지 (에러가 나야 정상)
-- BEGIN;
-- INSERT INTO automation.automation_jobs (raw_requirement, status)
-- VALUES ('제약 확인용', 'not a status');
-- ROLLBACK;
