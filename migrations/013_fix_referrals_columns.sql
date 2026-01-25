-- 013_fix_referrals_columns.sql

BEGIN;

-- 1. Гарантируем, что first_paid_at может быть NULL
ALTER TABLE referrals
    ALTER COLUMN first_paid_at DROP NOT NULL;

-- 2. Гарантируем, что reward_amount не отрицательный
ALTER TABLE referrals
    ADD CONSTRAINT chk_referrals_reward_amount_non_negative
    CHECK (reward_amount >= 0);

-- 3. Индекс для быстрых выборок по first_paid_at
CREATE INDEX IF NOT EXISTS idx_referrals_first_paid_at
    ON referrals (first_paid_at);

COMMIT;