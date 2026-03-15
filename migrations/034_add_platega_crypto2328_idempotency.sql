-- Migration 034: Add Platega and 2328.io payment idempotency keys
-- Purpose: Prevent duplicate balance credits for Platega (SBP) and 2328.io (crypto) providers
-- Safe for asyncpg (statement-by-statement execution)

-- 1. Add columns (idempotent)
ALTER TABLE payments
ADD COLUMN IF NOT EXISTS platega_payment_id TEXT;

ALTER TABLE payments
ADD COLUMN IF NOT EXISTS crypto2328_payment_id TEXT;

-- 2. Create UNIQUE indexes (partial, idempotent)
CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_platega_payment_id
ON payments (platega_payment_id)
WHERE platega_payment_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_crypto2328_payment_id
ON payments (crypto2328_payment_id)
WHERE crypto2328_payment_id IS NOT NULL;
