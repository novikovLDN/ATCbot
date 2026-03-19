-- Migration 035: Allow business tariffs in pending_purchases CHECK constraint
-- Previously only 'basic' and 'plus' were allowed, blocking biz_* purchases.

ALTER TABLE pending_purchases DROP CONSTRAINT IF EXISTS pending_purchases_tariff_check;
ALTER TABLE pending_purchases ADD CONSTRAINT pending_purchases_tariff_check
    CHECK (tariff IN ('basic', 'plus', 'biz_client_25', 'biz_client_50', 'biz_client_100', 'biz_client_150', 'biz_client_250', 'biz_client_500'));
