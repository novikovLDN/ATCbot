-- Migration 037: Allow telegram_premium in pending_purchases CHECK constraints
-- Adds 'telegram_premium' to both purchase_type and tariff checks.

ALTER TABLE pending_purchases DROP CONSTRAINT IF EXISTS pending_purchases_purchase_type_check;
ALTER TABLE pending_purchases ADD CONSTRAINT pending_purchases_purchase_type_check
    CHECK (purchase_type IN ('subscription', 'balance_topup', 'gift', 'telegram_premium'));

ALTER TABLE pending_purchases DROP CONSTRAINT IF EXISTS pending_purchases_tariff_check;
ALTER TABLE pending_purchases ADD CONSTRAINT pending_purchases_tariff_check
    CHECK (tariff IS NULL OR tariff IN ('basic', 'plus', 'biz_starter', 'biz_team', 'biz_business', 'biz_pro', 'biz_enterprise', 'biz_ultimate', 'telegram_premium'));
