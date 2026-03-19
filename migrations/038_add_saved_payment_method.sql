-- Migration 038: Add saved payment method support for YooKassa recurring payments
--
-- Adds columns to store saved YooKassa payment method for card-based auto-renewal.
-- saved_payment_method_id: YooKassa payment method ID for recurring charges
-- auto_renew_card: whether to auto-renew via saved card (separate from balance auto_renew)
-- saved_payment_method_title: card mask/title for display (e.g. "Visa •••• 4242")
--
-- Rollback safe: New columns are nullable, existing code ignores them.

ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS saved_payment_method_id TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS auto_renew_card BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS saved_payment_method_title TEXT DEFAULT NULL;

-- Index for auto-renewal worker to efficiently find subscriptions with saved cards
CREATE INDEX IF NOT EXISTS idx_subscriptions_auto_renew_card
    ON subscriptions (auto_renew_card)
    WHERE auto_renew_card = TRUE AND saved_payment_method_id IS NOT NULL;
