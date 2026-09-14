-- Migration 091: the users(trial_expires_at) index on EVERY database (follow-up to 090).
--
-- 090 created idx_users_trial_expires_at only when users.trial_expires_at
-- already existed. On an EMPTY database that column is created by the inline
-- DDL in database/core.py, which runs AFTER the migrations — 090 skipped the
-- index and was recorded as applied, so fresh environments never got it.
--
-- ADDITIVE and idempotent:
--   * the column, with the same type as the inline DDL (ADD COLUMN IF NOT
--     EXISTS — a no-op where it exists, e.g. production);
--   * the index, the same definition as 090 (IF NOT EXISTS — a no-op where
--     090 already built it).
--
-- Rollback: DROP INDEX IF EXISTS idx_users_trial_expires_at; the column stays
-- (the inline DDL and the code own it).

ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_expires_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_users_trial_expires_at
    ON users (trial_expires_at) WHERE trial_expires_at IS NOT NULL;
