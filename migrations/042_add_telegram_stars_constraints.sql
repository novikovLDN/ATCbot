-- Migration 042: Add telegram_stars and apple_id to pending_purchases CHECK constraints
-- Also adds bypass_% pattern to tariff_check for bypass-only traffic packs.

ALTER TABLE pending_purchases DROP CONSTRAINT IF EXISTS pending_purchases_purchase_type_check;
ALTER TABLE pending_purchases ADD CONSTRAINT pending_purchases_purchase_type_check
    CHECK (purchase_type IN ('subscription', 'balance_topup', 'gift', 'telegram_premium', 'telegram_stars', 'traffic_pack', 'apple_id'));

ALTER TABLE pending_purchases DROP CONSTRAINT IF EXISTS pending_purchases_tariff_check;
ALTER TABLE pending_purchases ADD CONSTRAINT pending_purchases_tariff_check
    CHECK (tariff IS NULL OR tariff IN ('basic', 'plus', 'biz_starter', 'biz_team', 'biz_business', 'biz_pro', 'biz_enterprise', 'biz_ultimate', 'telegram_premium', 'telegram_stars') OR tariff LIKE 'traffic_%' OR tariff LIKE 'apple_id_%' OR tariff LIKE 'bypass_%');
