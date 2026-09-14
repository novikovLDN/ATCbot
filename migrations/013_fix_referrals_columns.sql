-- 013_fix_referrals_columns.sql
--
-- No own BEGIN/COMMIT: migrations.py already wraps each file in a transaction.
-- Idempotent and safe on an empty database: first_paid_at is otherwise created
-- only by the inline schema in database/core.py, which runs AFTER migrations.

-- 0. Колонка может ещё не существовать (пустая БД)
ALTER TABLE referrals
    ADD COLUMN IF NOT EXISTS first_paid_at TIMESTAMP;

-- 1. Гарантируем, что first_paid_at может быть NULL
ALTER TABLE referrals
    ALTER COLUMN first_paid_at DROP NOT NULL;

-- 2. Гарантируем, что reward_amount не отрицательный
ALTER TABLE referrals
    ADD COLUMN IF NOT EXISTS reward_amount INTEGER DEFAULT 0;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_referrals_reward_amount_non_negative'
    ) THEN
        ALTER TABLE referrals
            ADD CONSTRAINT chk_referrals_reward_amount_non_negative
            CHECK (reward_amount >= 0);
    END IF;
END $$;

-- 3. Индекс для быстрых выборок по first_paid_at
CREATE INDEX IF NOT EXISTS idx_referrals_first_paid_at
    ON referrals (first_paid_at);
