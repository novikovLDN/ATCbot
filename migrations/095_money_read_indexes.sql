-- Migration 095: indexes for the money reads of the dashboard.
--
-- pending_purchases had status / telegram_id / purchase_id / expires_at only
-- (migration 004) and balance_transactions nothing but its primary key
-- (migration 002). Every money figure filters those two tables by a time
-- window — revenue totals, the daily series, payers, cohorts, provider
-- performance, the «Сегодня» tile and the revenue-milestone notifier — so each
-- of them scanned the whole table. The tables grow with every invoice and every
-- wallet movement, and the dashboard shares the bot's pool: a 5 s
-- statement_timeout (database/readonly.py) is what the owner sees as an empty
-- screen.
--
-- Shapes covered:
--   pending_purchases (status, created_at) — "paid in this window" (totals,
--       series, payers, /metrics/today);
--   pending_purchases (created_at)         — provider performance, which counts
--       invoices of every status in the window;
--   balance_transactions (type, created_at) — native top-ups and balance spend;
--   balance_transactions (user_id, type)    — per-user cashback / wallet reads.
--
-- ADDITIVE and idempotent. The runner wraps a migration in a transaction, so
-- CONCURRENTLY is not possible here: on production create them BEFORE the
-- deploy with CREATE INDEX CONCURRENTLY (docs/RUNBOOK.md) and this file is a
-- no-op there.
--
-- Rollback: DROP INDEX IF EXISTS idx_pending_purchases_status_created;
--           DROP INDEX IF EXISTS idx_pending_purchases_created_at;
--           DROP INDEX IF EXISTS idx_balance_transactions_type_created;
--           DROP INDEX IF EXISTS idx_balance_transactions_user_type;

CREATE INDEX IF NOT EXISTS idx_pending_purchases_status_created
    ON pending_purchases (status, created_at);

CREATE INDEX IF NOT EXISTS idx_pending_purchases_created_at
    ON pending_purchases (created_at);

CREATE INDEX IF NOT EXISTS idx_balance_transactions_type_created
    ON balance_transactions (type, created_at);

CREATE INDEX IF NOT EXISTS idx_balance_transactions_user_type
    ON balance_transactions (user_id, type);
