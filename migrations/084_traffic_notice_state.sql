-- Migration 084: users.traffic_notice_floor_bytes / users.traffic_notice_last_at
--
-- Bypass traffic notices (owner decision 2026-09-14): thresholds of the
-- REMAINING bypass GB — 50 / 30 / 15 / 10 / 5 / 3 / 1 — only those strictly
-- below the amount left at the last GB grant apply; one message per check (the
-- lowest threshold crossed, the higher ones are marked with it); at least 3 h
-- between two traffic messages; a GB grant / top-up re-arms them.
--
-- traffic_notice_floor_bytes: NULL = not observed since the last GB grant (the
--   next check records the amount left as the baseline, no message); otherwise
--   thresholds >= floor are done (0 = told «трафик закончился»; more GB than
--   the floor = a top-up → a new baseline).
-- traffic_notice_last_at: the last traffic message (naive UTC).
--
-- Additive and nullable, no backfill (every existing row starts at NULL: the
-- first check only records the baseline — no burst of messages at rollout).
-- Backward compatible; rollback = ignore the columns (the worker skips its pass
-- while they are missing).

ALTER TABLE users ADD COLUMN IF NOT EXISTS traffic_notice_floor_bytes BIGINT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS traffic_notice_last_at TIMESTAMP;
