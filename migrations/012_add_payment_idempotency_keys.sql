-- Migration 012: Add payment idempotency keys
-- Purpose: Prevent duplicate balance credits
-- Safe for asyncpg (statement-by-statement execution)

-- 1. Add columns (idempotent)
ALTER TABLE payments
ADD COLUMN IF NOT EXISTS telegram_payment_charge_id TEXT;

ALTER TABLE payments
ADD COLUMN IF NOT EXISTS cryptobot_payment_id TEXT;

-- 2. Create UNIQUE indexes (partial, idempotent)
CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_telegram_charge_id
ON payments (telegram_payment_charge_id)
WHERE telegram_payment_charge_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_cryptobot_payment_id
ON payments (cryptobot_payment_id)
WHERE cryptobot_payment_id IS NOT NULL;

-- NOTE:
-- COMMENT ON COLUMN intentionally REMOVED
-- asyncpg executes statements independently
-- comments are non-critical for production safety
