-- Migration 044: Add 'steam' purchase type + 'steam_%' tariff pattern
--
-- New shop product "Пополнить Steam" — same delivery model as Apple ID
-- (no automatic provisioning; admin notifies and tops up manually).
-- pending_purchases.purchase_type = 'steam'
-- pending_purchases.tariff = 'steam_<amount_rubles>'  (e.g. steam_1500)

ALTER TABLE pending_purchases DROP CONSTRAINT IF EXISTS pending_purchases_purchase_type_check;
ALTER TABLE pending_purchases ADD CONSTRAINT pending_purchases_purchase_type_check
    CHECK (purchase_type IN (
        'subscription', 'balance_topup', 'gift', 'telegram_premium',
        'telegram_stars', 'traffic_pack', 'apple_id', 'steam'
    ));

ALTER TABLE pending_purchases DROP CONSTRAINT IF EXISTS pending_purchases_tariff_check;
ALTER TABLE pending_purchases ADD CONSTRAINT pending_purchases_tariff_check
    CHECK (
        tariff IS NULL
        OR tariff IN (
            'basic', 'plus',
            'biz_starter', 'biz_team', 'biz_business', 'biz_pro', 'biz_enterprise', 'biz_ultimate',
            'telegram_premium', 'telegram_stars'
        )
        OR tariff LIKE 'traffic_%'
        OR tariff LIKE 'apple_id_%'
        OR tariff LIKE 'bypass_%'
        OR tariff LIKE 'steam_%'
    );
