-- =====================================================================
-- [V003] mart 스키마 — 외래키(FK) 추가
-- 실행 시점: V002(테이블 생성) 완료 후, 데이터 적재 전
-- 실행: psql -v ON_ERROR_STOP=1 -U hanacard_admin -d hanacard \
--             -f migrations/V003__mart_foreign_keys.sql
--
-- 참조 구조:
--   mcc_codes ←─ merchants ←─┐
--       ↑                     ├─ transactions
--       └─────────────────────┤
--   customers ←─ cards ←──────┘
--
-- 설계 메모: transactions는 customer_id를 직접 갖지 않고 cards를 경유한다.
--   그 결과 "고객과 카드의 짝이 맞지 않는 거래"라는 오류 자체가 성립하지 않는다
--   (초기 설계에서는 FK만으로 막지 못해 트리거가 필요했던 부분).
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. merchants -> mcc_codes
-- ---------------------------------------------------------------------
ALTER TABLE mart.merchants
    ADD CONSTRAINT fk_merchants_mcc FOREIGN KEY (mcc_code)
        REFERENCES mart.mcc_codes (mcc_code)
        ON UPDATE CASCADE ON DELETE RESTRICT;

-- ---------------------------------------------------------------------
-- 2. cards -> customers
--    카드는 반드시 소유 고객이 있어야 함 (customer_id NOT NULL)
-- ---------------------------------------------------------------------
ALTER TABLE mart.cards
    ADD CONSTRAINT fk_cards_customer FOREIGN KEY (customer_id)
        REFERENCES mart.customers (customer_id)
        ON UPDATE CASCADE ON DELETE RESTRICT;

-- ---------------------------------------------------------------------
-- 3. transactions -> cards
--    모든 거래는 반드시 카드로 발생함 (card_number_masked NOT NULL)
-- ---------------------------------------------------------------------
ALTER TABLE mart.transactions
    ADD CONSTRAINT fk_tx_card FOREIGN KEY (card_number_masked)
        REFERENCES mart.cards (card_number_masked)
        ON UPDATE CASCADE ON DELETE RESTRICT;

-- ---------------------------------------------------------------------
-- 4. transactions -> merchants
--    merchant_id는 NULL 허용(해외거래). SQL 표준상 FK 컬럼이 NULL이면
--    참조 검사를 건너뛰므로 해외거래는 FK 위반이 아니다.
-- ---------------------------------------------------------------------
ALTER TABLE mart.transactions
    ADD CONSTRAINT fk_tx_merchant FOREIGN KEY (merchant_id)
        REFERENCES mart.merchants (merchant_id)
        ON UPDATE CASCADE ON DELETE RESTRICT;

-- ---------------------------------------------------------------------
-- 5. transactions -> mcc_codes
--    해외거래(merchant_id IS NULL)도 업종 코드는 반드시 존재하므로 NOT NULL.
--    이 FK 덕분에 해외 전용 업종도 업종명을 조회할 수 있다.
-- ---------------------------------------------------------------------
ALTER TABLE mart.transactions
    ADD CONSTRAINT fk_tx_mcc FOREIGN KEY (mcc_code)
        REFERENCES mart.mcc_codes (mcc_code)
        ON UPDATE CASCADE ON DELETE RESTRICT;

-- ---------------------------------------------------------------------
-- 결과 확인
-- ---------------------------------------------------------------------
SELECT conrelid::regclass::text AS "테이블",
       conname                  AS "제약명",
       pg_get_constraintdef(oid) AS "정의"
FROM pg_constraint
WHERE connamespace = 'mart'::regnamespace AND contype = 'f'
ORDER BY 1, 2;
