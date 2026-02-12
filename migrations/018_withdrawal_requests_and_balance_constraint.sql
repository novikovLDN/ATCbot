-- Migration 018: Withdrawal requests + balance non-negative constraint
-- Atlas Secure: Balance management + withdrawal system
--
-- Creates withdrawal_requests table for user withdrawal flow.
-- Adds balance_non_negative constraint to users.

-- withdrawal_requests: pending user withdrawal requests
CREATE TABLE IF NOT EXISTS withdrawal_requests (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL,
    username TEXT,
    amount INTEGER NOT NULL,  -- копейки
    requisites TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMP,
    processed_by BIGINT
);

CREATE INDEX IF NOT EXISTS idx_withdrawal_requests_telegram_id ON withdrawal_requests(telegram_id);
CREATE INDEX IF NOT EXISTS idx_withdrawal_requests_status ON withdrawal_requests(status);

-- balance_non_negative: запрет отрицательного баланса на уровне БД
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'users') THEN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'balance_non_negative'
            AND conrelid = 'users'::regclass
        ) THEN
            ALTER TABLE users ADD CONSTRAINT balance_non_negative CHECK (balance >= 0);
        END IF;
    END IF;
END $$;
