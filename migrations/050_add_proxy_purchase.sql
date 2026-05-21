-- Migration 050: Standalone Telegram MTProto-proxy product
--
-- New product "Telegram-прокси" — a one-time, permanent purchase (69₽) that
-- delivers a single static tg://proxy link shared by all buyers. It does NOT
-- activate a VPN subscription and is available to users without one.
--
-- pending_purchases.purchase_type = 'proxy'
-- pending_purchases.tariff        = 'proxy'
-- users.proxy_purchased_at        — non-NULL once the user owns the proxy.

ALTER TABLE pending_purchases DROP CONSTRAINT IF EXISTS pending_purchases_purchase_type_check;
ALTER TABLE pending_purchases ADD CONSTRAINT pending_purchases_purchase_type_check
    CHECK (purchase_type IN (
        'subscription', 'balance_topup', 'gift', 'telegram_premium',
        'telegram_stars', 'traffic_pack', 'apple_id', 'steam', 'proxy'
    ));

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
    );

ALTER TABLE users ADD COLUMN IF NOT EXISTS proxy_purchased_at TIMESTAMP;
