-- Migration 082: provisioning_jobs — outbox for premium/bypass provisioning
-- (docs/audit/02_payment_core_plan.md §B, §C).
--
-- A job is written in the SAME transaction as the billing record, keyed by a
-- UNIQUE idempotency_key ("purchase:{id}", "balance:{payment_id}", ...).
-- The worker applies it to the Remnawave panel after commit.
--
-- Additive and idempotent (IF NOT EXISTS everywhere): safe to re-run and to
-- apply on an empty DB. Timestamps are TIMESTAMP WITHOUT TIME ZONE in UTC
-- (write via _to_db_utc, read via _from_db_utc).
-- Rollback: code that does not know the table simply ignores it; DROP only in
-- a separate release.
-- Number 081 is taken in the atcnew branch — hence 082.

CREATE TABLE IF NOT EXISTS provisioning_jobs (
    id                  BIGSERIAL PRIMARY KEY,
    idempotency_key     TEXT        NOT NULL UNIQUE,
    telegram_id         BIGINT      NOT NULL,
    source              TEXT        NOT NULL,
    tariff_key          TEXT        NOT NULL,
    premium_until       TIMESTAMP   NULL,
    bypass_add_bytes    BIGINT      NOT NULL DEFAULT 0 CHECK (bypass_add_bytes >= 0),
    bypass_base_bytes   BIGINT      NULL,
    bypass_target_bytes BIGINT      NULL,
    status              TEXT        NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','running','done','dead','shadow')),
    attempts            INTEGER     NOT NULL DEFAULT 0,
    next_attempt_at     TIMESTAMP   NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    lease_until         TIMESTAMP   NULL,
    last_error          TEXT        NULL,
    context             JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMP   NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    updated_at          TIMESTAMP   NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    done_at             TIMESTAMP   NULL
);
CREATE INDEX IF NOT EXISTS idx_prov_jobs_due  ON provisioning_jobs (next_attempt_at) WHERE status IN ('pending','running');
CREATE INDEX IF NOT EXISTS idx_prov_jobs_user ON provisioning_jobs (telegram_id, id);

-- Recurring Platega subscriptions: remember that the plan was a combo tariff
-- (pending_purchases.tariff CHECK cannot hold combo_* keys, see plan fact #2).
ALTER TABLE platega_subscriptions ADD COLUMN IF NOT EXISTS is_combo BOOLEAN NOT NULL DEFAULT FALSE;
