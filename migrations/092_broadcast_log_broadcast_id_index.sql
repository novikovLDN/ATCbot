-- Migration 092: index broadcast_log by broadcast_id (+ status).
--
-- Every broadcast_log read filters by broadcast_id (delivery counts, the
-- dashboard engagement metrics, delete-sent-messages) and most also by status.
-- Without an index each read scans the whole log (one row per recipient per
-- broadcast): the dashboard engagement query hit the statement timeout on
-- production (2026-09-14, database/metrics.py engagement → QueryCanceledError).
--
-- ADDITIVE and idempotent. The runner wraps a migration in a transaction, so
-- CONCURRENTLY is not possible here: on production create the index BEFORE
-- the deploy with
--   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_broadcast_log_broadcast_status
--       ON broadcast_log (broadcast_id, status);
-- and this file is then a no-op (docs/RUNBOOK.md).
--
-- Rollback: DROP INDEX IF EXISTS idx_broadcast_log_broadcast_status;

CREATE INDEX IF NOT EXISTS idx_broadcast_log_broadcast_status
    ON broadcast_log (broadcast_id, status);
