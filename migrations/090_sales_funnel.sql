-- Migration 090: sales funnel (owner 2026-09-14, docs/audit/SCOPE.md «Воронка продаж»).
--
-- ADDITIVE ONLY: one new table + indexes. No existing row is read or changed.
--
-- funnel_messages — one row per (user, chain, event, step). The worker CLAIMS
-- the row (INSERT … ON CONFLICT DO NOTHING) before it sends, so a restart or
-- an overlapping pass never sends the same step twice. `anchor_at` is the
-- event the chain counts from (the /start, the trial end, the end of the paid
-- subscription): a user whose paid subscription ends twice runs chain «paid»
-- twice, each with its own steps.
--   status: claimed (being sent) | sent | failed (Telegram refused) |
--           skipped (step disabled in the dashboard, overtaken by a later
--           step, or its discount window already over)
--
-- The cut-off «only events after the deploy» lives in app_settings
-- (key sales_funnel_started_at), written by the worker's first pass.
--
-- Rollback: DROP TABLE funnel_messages; the indexes below are plain lookups.

CREATE TABLE IF NOT EXISTS funnel_messages (
    id                  BIGSERIAL PRIMARY KEY,
    telegram_id         BIGINT      NOT NULL,
    chain               TEXT        NOT NULL,
    anchor_at           TIMESTAMPTZ NOT NULL,
    step                TEXT        NOT NULL,
    status              TEXT        NOT NULL DEFAULT 'claimed',
    discount_percent    INTEGER,
    discount_expires_at TIMESTAMPTZ,
    sent_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_funnel_messages_step UNIQUE (telegram_id, chain, anchor_at, step),
    CONSTRAINT chk_funnel_messages_chain CHECK (chain IN ('start', 'trial', 'paid')),
    CONSTRAINT chk_funnel_messages_status CHECK (status IN ('claimed', 'sent', 'failed', 'skipped'))
);

-- «≤ 1 funnel message per user per day»: the claim looks up the user's rows.
CREATE INDEX IF NOT EXISTS idx_funnel_messages_user_sent
    ON funnel_messages (telegram_id, sent_at DESC);

-- Chain «paid» finds subscriptions that ended since the cut-off through the
-- history end dates (the subscriptions row of a bypass-only user carries a
-- +10 years placeholder, the history keeps the real end).
CREATE INDEX IF NOT EXISTS idx_subscription_history_end_date
    ON subscription_history (end_date);

-- Chain «trial» finds trials that ended since the cut-off. users.trial_expires_at
-- is created by the inline DDL in database/core.py, which runs AFTER the
-- migrations — on an empty database the column does not exist yet here.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'trial_expires_at'
    ) THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_users_trial_expires_at '
                'ON users (trial_expires_at) WHERE trial_expires_at IS NOT NULL';
    END IF;
END $$;
