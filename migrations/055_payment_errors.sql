-- Migration 055: payment_errors log for dashboard visibility
-- Each row is one failed payment attempt — webhook validation reject,
-- amount mismatch, provisioning failure, idempotency rejection, etc.
-- Used by /dashboard/api/payments/errors and shown on the Payments page.

CREATE TABLE IF NOT EXISTS payment_errors (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT,
    purchase_id TEXT,
    payment_provider TEXT,
    amount_rubles NUMERIC(12, 2),
    stage TEXT NOT NULL,
    error_code TEXT,
    error_message TEXT,
    raw_payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_payment_errors_created_at
    ON payment_errors (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_payment_errors_telegram_id
    ON payment_errors (telegram_id)
    WHERE telegram_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_payment_errors_provider
    ON payment_errors (payment_provider)
    WHERE payment_provider IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_payment_errors_stage
    ON payment_errors (stage);
