-- Migration 051: Standalone Telegram MTProto-proxy product
--
-- New product "Telegram-прокси" — a one-time, permanent purchase that
-- delivers a single static proxy link shared by all buyers. It does NOT
-- activate a VPN subscription and is available to users without one.
--
-- pending_purchases.purchase_type = 'proxy'
-- pending_purchases.tariff        = 'proxy'
-- users.proxy_purchased_at        — non-NULL once the user owns the proxy.
--
-- NOTE: numbered 051 (not 050) because version "050" was already recorded
-- in some deployed schema_migrations tables — a reused number would be
-- silently skipped. The CHECK constraints are added NOT VALID so the
-- migration can never fail on pre-existing rows; new inserts are still
-- validated.

ALTER TABLE users ADD COLUMN IF NOT EXISTS proxy_purchased_at TIMESTAMP;

ALTER TABLE pending_purchases DROP CONSTRAINT IF EXISTS pending_purchases_purchase_type_check;
ALTER TABLE pending_purchases ADD CONSTRAINT pending_purchases_purchase_type_check
    CHECK (purchase_type IN (
        'subscription', 'balance_topup', 'gift', 'telegram_premium',
        'telegram_stars', 'traffic_pack', 'apple_id', 'steam', 'proxy'
    )) NOT VALID;

ALTER TABLE pending_purchases DROP CONSTRAINT IF EXISTS pending_purchases_tariff_check;
ALTER TABLE pending_purchases ADD CONSTRAINT pending_purchases_tariff_check
    CHECK (
        tariff IS NULL
        OR tariff IN (
            'basic', 'plus',
            'biz_starter', 'biz_team', 'biz_business', 'biz_pro', 'biz_enterprise', 'biz_ultimate',
            'telegram_premium', 'telegram_stars', 'proxy'
        )
        OR tariff LIKE 'traffic_%'
        OR tariff LIKE 'apple_id_%'
        OR tariff LIKE 'bypass_%'
        OR tariff LIKE 'steam_%'
    ) NOT VALID;
