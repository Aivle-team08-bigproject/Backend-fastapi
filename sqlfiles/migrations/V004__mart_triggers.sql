-- =====================================================================
-- [V004] mart 스키마 — 정합성 검증 트리거
-- 실행 시점: V003(FK) 완료 후, 데이터 적재 "전"
--            ※ 적재 전에 설치해야 부적합 데이터를 차단할 수 있음
-- 실행: psql -v ON_ERROR_STOP=1 -U portfolio_admin -d portfolio \
--             -f migrations/V004__mart_triggers.sql
--
-- 왜 CHECK가 아니라 트리거인가:
--   PostgreSQL의 CHECK 제약은 자기 행(NEW) 안에서만 판단 가능하며
--   다른 테이블을 참조하는 서브쿼리를 쓸 수 없다.
--   아래 규칙은 모두 cards/merchants를 조회해야 하므로 트리거로 구현한다.
--   같은 행 안에서 판단 가능한 규칙(채널↔매체, 인증↔매체, 국내↔통화 등)은
--   V002의 CHECK 제약으로 구현했다 — 트리거보다 가볍고 옵티마이저가 활용할 수 있기 때문.
--
-- 설계 메모(초기 설계 대비):
--   추가: 카드 해지월 이후 거래 차단, 가맹점 폐업월 이후 거래 차단
--         (상태변경월 컬럼이 생기면서 판정이 가능해짐)
--   제외: 카드 소유자 일치 검증 — transactions.customer_id를 없애 오류 자체가 불가능
--   제외: 국내거래 통화 검증 — 다른 테이블 참조가 불필요하여 CHECK로 이동
--
-- 대량 적재 시:
--   트리거는 행마다 실행되므로 수백만 건 초기 적재 시 병목이 될 수 있다.
--   그 경우 ALTER TABLE ... DISABLE TRIGGER 로 끄고 적재한 뒤,
--   V5의 검증 쿼리로 사후 확인하고 다시 ENABLE 하는 방식을 쓸 것.
-- =====================================================================

-- ---------------------------------------------------------------------
-- [1] 거래일시 >= 카드 발급월
--     존재하지 않는 카드로 결제할 수 없다.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION mart.fn_tx_after_card_issue()
RETURNS trigger AS $$
DECLARE
    v_issue VARCHAR(7);
BEGIN
    SELECT card_issue_month INTO v_issue
    FROM mart.cards WHERE card_number_masked = NEW.card_number_masked;

    IF v_issue IS NOT NULL
       AND to_char(NEW.transaction_datetime, 'YYYY-MM') < v_issue THEN
        RAISE EXCEPTION
            '[정합성 위반] 카드 발급 전 거래: transaction_id=%, 거래=%, 발급월=%',
            NEW.transaction_id, to_char(NEW.transaction_datetime, 'YYYY-MM'), v_issue
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION mart.fn_tx_after_card_issue() IS
'거래일시가 카드 발급월보다 이르면 거부. cards 참조가 필요해 CHECK로 구현 불가.';

CREATE TRIGGER trg_tx_after_card_issue
    BEFORE INSERT OR UPDATE ON mart.transactions
    FOR EACH ROW EXECUTE FUNCTION mart.fn_tx_after_card_issue();

-- ---------------------------------------------------------------------
-- [2] 해지된 카드는 해지월 이후 거래 불가
--     해지는 되돌릴 수 없는 종료 상태다.
--     일시정지는 해제 후 재사용이 가능하므로 검사하지 않는다.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION mart.fn_tx_before_card_terminated()
RETURNS trigger AS $$
DECLARE
    v_status  VARCHAR(10);
    v_changed VARCHAR(7);
BEGIN
    SELECT card_status, card_status_changed_month INTO v_status, v_changed
    FROM mart.cards WHERE card_number_masked = NEW.card_number_masked;

    IF v_status = '해지' AND v_changed IS NOT NULL
       AND to_char(NEW.transaction_datetime, 'YYYY-MM') > v_changed THEN
        RAISE EXCEPTION
            '[정합성 위반] 해지된 카드의 해지 후 거래: transaction_id=%, 거래=%, 해지월=%',
            NEW.transaction_id, to_char(NEW.transaction_datetime, 'YYYY-MM'), v_changed
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION mart.fn_tx_before_card_terminated() IS
'해지된 카드로 해지월 이후에 발생한 거래를 거부.
일시정지는 해제 가능하므로 검사 대상이 아님 — card_status_changed_month는 최종 변경
시점만 기록하므로 "정지 → 해제 → 사용" 이력을 구분할 수 없기 때문.';

CREATE TRIGGER trg_tx_before_card_terminated
    BEFORE INSERT OR UPDATE ON mart.transactions
    FOR EACH ROW EXECUTE FUNCTION mart.fn_tx_before_card_terminated();

-- ---------------------------------------------------------------------
-- [3] 거래일시가 가맹점 영업 기간 내인가
--     개업 전 결제 불가, 폐업 후 결제 불가.
--     휴업은 재개업이 가능하므로 검사하지 않는다.
--     merchant_id가 NULL인 해외거래는 검사 대상이 아니다.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION mart.fn_tx_merchant_active()
RETURNS trigger AS $$
DECLARE
    v_open    VARCHAR(7);
    v_status  VARCHAR(10);
    v_changed VARCHAR(7);
    v_month   VARCHAR(7) := to_char(NEW.transaction_datetime, 'YYYY-MM');
BEGIN
    IF NEW.merchant_id IS NULL THEN
        RETURN NEW;   -- 해외거래: 국내 가맹점 마스터에 없으므로 검사 제외
    END IF;

    SELECT merchant_open_month, merchant_status, merchant_status_changed_month
      INTO v_open, v_status, v_changed
    FROM mart.merchants WHERE merchant_id = NEW.merchant_id;

    IF v_open IS NOT NULL AND v_month < v_open THEN
        RAISE EXCEPTION
            '[정합성 위반] 가맹점 개업 전 거래: transaction_id=%, 거래=%, 개업월=%',
            NEW.transaction_id, v_month, v_open
            USING ERRCODE = 'check_violation';
    END IF;

    IF v_status = '폐업' AND v_changed IS NOT NULL AND v_month > v_changed THEN
        RAISE EXCEPTION
            '[정합성 위반] 폐업 가맹점의 폐업 후 거래: transaction_id=%, 거래=%, 폐업월=%',
            NEW.transaction_id, v_month, v_changed
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION mart.fn_tx_merchant_active() IS
'거래일시가 가맹점 영업 기간(개업월 ~ 폐업월) 내인지 검증.
휴업은 재개업이 가능하므로 검사 대상이 아님. 해외거래(merchant_id IS NULL)는 검사 제외.';

CREATE TRIGGER trg_tx_merchant_active
    BEFORE INSERT OR UPDATE ON mart.transactions
    FOR EACH ROW EXECUTE FUNCTION mart.fn_tx_merchant_active();

-- ---------------------------------------------------------------------
-- [4] 국내거래 MCC 일치
--     merchant_id가 있으면 그 가맹점의 업종과 거래 업종이 같아야 한다.
--     해외거래는 가맹점 마스터가 없고 MCC가 승인 전문에서 오므로 검사 대상이 아니다.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION mart.fn_tx_mcc_match()
RETURNS trigger AS $$
DECLARE
    v_mcc INTEGER;
BEGIN
    IF NEW.merchant_id IS NULL THEN
        RETURN NEW;   -- 해외거래: MCC는 카드 승인 전문에서 오므로 마스터와 대조 불가
    END IF;

    SELECT mcc_code INTO v_mcc
    FROM mart.merchants WHERE merchant_id = NEW.merchant_id;

    IF v_mcc IS NOT NULL AND v_mcc <> NEW.mcc_code THEN
        RAISE EXCEPTION
            '[정합성 위반] 국내거래 MCC 불일치: transaction_id=%, 거래MCC=%, 가맹점MCC=%',
            NEW.transaction_id, NEW.mcc_code, v_mcc
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION mart.fn_tx_mcc_match() IS
'국내거래의 업종코드가 가맹점 마스터와 일치하는지 검증. 해외거래는 검사 제외.';

CREATE TRIGGER trg_tx_mcc_match
    BEFORE INSERT OR UPDATE ON mart.transactions
    FOR EACH ROW EXECUTE FUNCTION mart.fn_tx_mcc_match();

-- ---------------------------------------------------------------------
-- [5] 체크카드 할부 금지
--     체크카드는 결제 즉시 계좌에서 출금되므로 할부가 성립하지 않는다.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION mart.fn_tx_check_card_no_installment()
RETURNS trigger AS $$
DECLARE
    v_product VARCHAR(20);
BEGIN
    IF NEW.installment_months = 0 THEN
        RETURN NEW;   -- 일시불은 검사 불필요
    END IF;

    SELECT card_product_code INTO v_product
    FROM mart.cards WHERE card_number_masked = NEW.card_number_masked;

    IF v_product LIKE 'CK%' THEN
        RAISE EXCEPTION
            '[정합성 위반] 체크카드 할부 거래: transaction_id=%, 상품=%, 할부=%개월',
            NEW.transaction_id, v_product, NEW.installment_months
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION mart.fn_tx_check_card_no_installment() IS
'체크카드(CK로 시작하는 상품코드)의 할부 거래를 거부.
카드 상품 정보가 cards에 있어 CHECK로 구현 불가.';

CREATE TRIGGER trg_tx_check_card_no_installment
    BEFORE INSERT OR UPDATE ON mart.transactions
    FOR EACH ROW EXECUTE FUNCTION mart.fn_tx_check_card_no_installment();

-- ---------------------------------------------------------------------
-- 설치 결과 확인
-- ---------------------------------------------------------------------
SELECT tgname AS "트리거명", pg_get_triggerdef(oid) AS "정의"
FROM pg_trigger
WHERE tgrelid = 'mart.transactions'::regclass AND NOT tgisinternal
ORDER BY tgname;
