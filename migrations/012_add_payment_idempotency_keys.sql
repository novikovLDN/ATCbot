-- Migration 012: Add payment idempotency keys
-- Adds unique idempotency keys for Telegram and CryptoBot payments to prevent duplicate balance credits

-- Add idempotency columns to payments table
ALTER TABLE payments
ADD COLUMN IF NOT EXISTS telegram_payment_charge_id TEXT,
ADD COLUMN IF NOT EXISTS cryptobot_payment_id TEXT;

-- Create unique indexes for idempotency protection
-- These prevent duplicate balance credits from repeated webhooks
CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_telegram_charge_id 
ON payments(telegram_payment_charge_id) 
WHERE telegram_payment_charge_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_cryptobot_payment_id 
ON payments(cryptobot_payment_id) 
WHERE cryptobot_payment_id IS NOT NULL;

-- Add comment for documentation
COMMENT ON COLUMN payments.telegram_payment_charge_id IS 'Unique Telegram payment charge ID for idempotency (prevents duplicate balance credits)';
COMMENT ON COLUMN payments.cryptobot_payment_id IS 'Unique CryptoBot payment ID for idempotency (prevents duplicate balance credits)';
