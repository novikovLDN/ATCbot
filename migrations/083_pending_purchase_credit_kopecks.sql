-- Migration 083: pending_purchases.credit_kopecks
--
-- Balance top-up via SBP with SBP_MARKUP_PERCENT > 0: the user pays
-- amount + markup (price_kopecks), but only the amount they asked for is
-- credited to the balance (owner decision 2026-09-14). credit_kopecks holds
-- that amount; price_kopecks keeps what is charged, so revenue still counts
-- the money actually paid.
--
-- NULL (every existing row, every non-top-up row, a row written before this
-- column exists) = credit min(paid, price_kopecks) exactly as before.
-- Additive and nullable, no backfill: backward compatible; rollback = ignore
-- the column (code falls back when it is missing).

ALTER TABLE pending_purchases ADD COLUMN IF NOT EXISTS credit_kopecks INTEGER;
