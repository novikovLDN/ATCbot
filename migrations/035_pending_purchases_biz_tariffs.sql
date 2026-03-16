-- Migration 035: Allow business tariffs in pending_purchases CHECK constraint
-- Previously only 'basic' and 'plus' were allowed, blocking biz_* purchases.

ALTER TABLE pending_purchases DROP CONSTRAINT IF EXISTS pending_purchases_tariff_check;
ALTER TABLE pending_purchases ADD CONSTRAINT pending_purchases_tariff_check
    CHECK (tariff IN ('basic', 'plus', 'biz_starter', 'biz_team', 'biz_business', 'biz_pro', 'biz_enterprise', 'biz_ultimate'));
