-- Migration 054: track payment_provider on pending_purchases for analytics
-- Safe for asyncpg (statement-by-statement execution)

ALTER TABLE pending_purchases ADD COLUMN IF NOT EXISTS payment_provider TEXT;

CREATE INDEX IF NOT EXISTS idx_pending_purchases_payment_provider
    ON pending_purchases(payment_provider)
    WHERE payment_provider IS NOT NULL;

-- Backfill what we can from the payments table. Stars + CryptoBot have
-- their own identifier columns (telegram_payment_charge_id /
-- cryptobot_payment_id), so we can infer those.
UPDATE pending_purchases pp
SET payment_provider = 'telegram_stars'
FROM payments p
WHERE pp.purchase_id = p.purchase_id
  AND pp.payment_provider IS NULL
  AND p.telegram_payment_charge_id IS NOT NULL;

UPDATE pending_purchases pp
SET payment_provider = 'cryptobot'
FROM payments p
WHERE pp.purchase_id = p.purchase_id
  AND pp.payment_provider IS NULL
  AND p.cryptobot_payment_id IS NOT NULL;

-- Everything else (Platega card, Platega SBP, Lava) gets NULL — going-
-- forward writes will fill it in via the finalize_purchase update.
