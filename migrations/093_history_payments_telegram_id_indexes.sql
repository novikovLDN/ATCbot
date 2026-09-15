-- Migration 093: subscription_history (telegram_id, created_at DESC) and
-- payments (telegram_id) indexes.
--
-- get_subscriptions_for_reminders runs a correlated subquery into
-- subscription_history for every active subscription ("last action_type"), and
-- the funnel / reconciliation read payments by telegram_id. subscription_history
-- had only its PK and payments no telegram_id index: on production (~14k active
-- subscriptions) the reminders query took 26 s on 2026-09-14 and hit the 30 s
-- statement timeout on 2026-09-15 (TimeoutError in send_smart_reminders) — no
-- paid-subscription reminder went out. Synthetic 700k history rows: 30.2 s
-- without the index, 25 ms with it (docs/audit, pre-merge review).
--
-- ADDITIVE and idempotent. The runner wraps a migration in a transaction, so
-- CONCURRENTLY is not possible here: on production create both indexes BEFORE
-- the deploy with CREATE INDEX CONCURRENTLY (docs/RUNBOOK.md) and this file is a
-- no-op there.
--
-- Rollback: DROP INDEX IF EXISTS idx_subscription_history_tg_created;
--           DROP INDEX IF EXISTS idx_payments_telegram_id;

CREATE INDEX IF NOT EXISTS idx_subscription_history_tg_created
    ON subscription_history (telegram_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_payments_telegram_id
    ON payments (telegram_id);
