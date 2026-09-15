-- Migration 094: drop the stale pending_purchases purchase_type / tariff CHECKs.
--
-- Production (2026-09-15, pg_constraint) has NEITHER constraint: init_db()
-- dropped pending_purchases_tariff_check on every start and its narrower
-- re-ADD failed on existing rows; purchase_type_check was already gone. All
-- purchases (subscriptions, packs, shop, proxy, farm effects) run without them,
-- and the code validates purchase types / tariffs itself.
--
-- The migration lists (latest 052) never matched what the code writes (no
-- 'spotify' purchase type, no 'spotify_%' tariff), so a fresh DB built from
-- migrations kept checks production does not have. This makes every DB match
-- production. On production it is a no-op (IF EXISTS).
--
-- Rollback: none needed — re-adding the lists would block live products.

ALTER TABLE pending_purchases DROP CONSTRAINT IF EXISTS pending_purchases_purchase_type_check;
ALTER TABLE pending_purchases DROP CONSTRAINT IF EXISTS pending_purchases_tariff_check;
