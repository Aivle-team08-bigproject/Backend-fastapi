-- =====================================================================
-- [V012] anon 스키마 → anonymized 로 이름 변경
-- 대상: PostgreSQL 17, portfolio DB
--
-- 실행: psql -v ON_ERROR_STOP=1 -U portfolio_admin -d portfolio \
--             -f migrations/V012__rename_anon_schema.sql
-- 이전: V011 (automation 상태 CHECK 완화)
--
-- ---------------------------------------------------------------------
-- 왜 이름을 바꾸는가
-- ---------------------------------------------------------------------
-- `anon`은 PostgreSQL Anonymizer 확장이 쓰는 표준 스키마 이름이다.
-- 그래서 일부 관리형 PostgreSQL은 이 이름을 예약해두고 보호한다.
--
-- 실제로 2026-07-29 Neon에서 아래 에러로 스키마 구성이 중단됐다.
--
--     ERROR: cannot create trigger on relation "transactions":
--            triggers on the anon schema are not permitted
--
-- 같은 이름을 계속 쓰면 관리형 DB로 옮길 때마다 같은 벽에 부딪히고,
-- 나중에 PostgreSQL Anonymizer를 직접 도입할 경우에도 충돌한다.
-- (익명화 도구라 실제로 쓸 가능성이 있다)
--
-- mart / service / automation 은 예약어와 무관해 그대로 둔다.
--
-- ---------------------------------------------------------------------
-- 이 파일이 하는 일은 이름 변경 하나뿐이다
-- ---------------------------------------------------------------------
-- ALTER SCHEMA ... RENAME 은 객체의 OID를 바꾸지 않는다. 따라서
--   테이블·데이터·인덱스·제약           그대로 유지
--   COMMENT 47개 (V008)                 그대로 유지
--   트리거·트리거 함수 5종               그대로 유지
--   GRANT (agent_svc SELECT 등)          그대로 유지
--   ALTER DEFAULT PRIVILEGES (V001)      그대로 유지
-- 재적재나 익명화 배치 재실행이 필요 없다.
--
-- ---------------------------------------------------------------------
-- 신규 환경에서는 아무 일도 하지 않는다
-- ---------------------------------------------------------------------
-- V001/V005가 이미 `anonymized` 이름으로 스키마를 만들도록 수정됐으므로,
-- 새로 구축하는 환경에는 `anon`이 존재하지 않는다. 아래 가드가 그 경우를 건너뛴다.
-- =====================================================================

-- 판정 기준은 "anon 스키마가 있는가"가 아니라 "그 안에 우리 테이블이 있는가"다.
-- PostgreSQL Anonymizer 확장이 설치된 환경에는 우리와 무관한 anon 스키마가
-- 이미 존재할 수 있고, 그걸 건드리면 안 되기 때문이다.
DO $$
BEGIN
    IF to_regclass('anon.customers') IS NULL THEN
        RAISE NOTICE '[V012] 이름 변경 대상이 없다 — 신규 환경이거나 이미 적용됨. 건너뛴다.';

    ELSIF to_regclass('anonymized.customers') IS NOT NULL THEN
        -- 둘 다 있는 상태. 자동으로 합치면 데이터가 어느 쪽에 있는지 모호해지므로
        -- 사람이 판단해야 한다.
        RAISE EXCEPTION
            '[V012] anon 과 anonymized 가 모두 존재한다. 수동 확인이 필요하다. '
            '각각의 행 수를 비교해 쓰지 않는 쪽을 DROP한 뒤 다시 실행할 것: '
            'SELECT ''anon'' s, count(*) FROM anon.customers '
            'UNION ALL SELECT ''anonymized'', count(*) FROM anonymized.customers;';

    ELSE
        EXECUTE 'ALTER SCHEMA anon RENAME TO anonymized';
        RAISE NOTICE '[V012] anon -> anonymized 이름 변경 완료.';
    END IF;
END $$;

-- =====================================================================
-- 검증 (실행 후 확인용)
-- =====================================================================
-- -- anon 은 없고 anonymized 만 있어야 한다
-- SELECT nspname, pg_get_userbyid(nspowner) AS owner
--   FROM pg_namespace WHERE nspname IN ('anon','anonymized');
--
-- -- 테이블 5개가 그대로 있는지
-- SELECT tablename FROM pg_tables WHERE schemaname='anonymized' ORDER BY 1;
--
-- -- 코멘트 47개가 살아있는지 (40 초과면 정상)
-- SELECT count(*) FROM pg_description d
--   JOIN pg_class c     ON c.oid = d.objoid
--   JOIN pg_namespace n ON n.oid = c.relnamespace
--  WHERE n.nspname = 'anonymized';
--
-- -- 트리거 5개가 살아있는지
-- SELECT count(*) FROM pg_trigger t
--   JOIN pg_class c     ON c.oid = t.tgrelid
--   JOIN pg_namespace n ON n.oid = c.relnamespace
--  WHERE n.nspname = 'anonymized' AND NOT t.tgisinternal;
--
-- -- agent_svc 권한이 유지됐는지 (true여야 정상)
-- SELECT has_schema_privilege('agent_svc','anonymized','USAGE') AS usage,
--        has_table_privilege('agent_svc','anonymized.customers','SELECT') AS select_ok;
--
-- -- mart 접근은 여전히 막혀 있는지 (false여야 정상)
-- SELECT has_schema_privilege('agent_svc','mart','USAGE');
