-- 041: Fix traffic pack support
-- 1) Ensure payment_method column exists on traffic_purchases
ALTER TABLE traffic_purchases ADD COLUMN IF NOT EXISTS payment_method TEXT;

-- 2) Add 'traffic_pack' to pending_purchases purchase_type CHECK constraint
ALTER TABLE pending_purchases DROP CONSTRAINT IF EXISTS pending_purchases_purchase_type_check;
ALTER TABLE pending_purchases ADD CONSTRAINT pending_purchases_purchase_type_check
    CHECK (purchase_type IN ('subscription', 'balance_topup', 'gift', 'telegram_premium', 'traffic_pack'));

-- 3) Allow traffic_Ngb tariff values in pending_purchases tariff CHECK
ALTER TABLE pending_purchases DROP CONSTRAINT IF EXISTS pending_purchases_tariff_check;
ALTER TABLE pending_purchases ADD CONSTRAINT pending_purchases_tariff_check
    CHECK (tariff IS NULL OR tariff IN ('basic', 'plus', 'biz_starter', 'biz_team', 'biz_business', 'biz_pro', 'biz_enterprise', 'biz_ultimate', 'telegram_premium') OR tariff LIKE 'traffic_%');
