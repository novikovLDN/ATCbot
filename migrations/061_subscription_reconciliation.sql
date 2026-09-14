-- Migration 061: Subscription reconciliation & over-issuance tracking
--
-- Two audit tables to help the admin dashboard's «Сверка» screen:
--
-- 1. subscription_over_issuance_log — auto-populated by grant_access whenever
--    ANY premium subscription is written with expires_at > NOW + 8y. Captures
--    everything we know at write-time (source, tariff, admin id, admin_grant_days,
--    caller_context) so the dashboard can retrace where the over-issuance came
--    from. Also fires an admin Telegram alert with the same context (see
--    app/services/subscription_watchdog.py).
--
-- 2. subscription_reconciliation_log — populated by the /fix endpoint each time
--    an admin manually shortens a subscription based on the sum of the user's
--    approved subscription payments + any admin_grant_days. Stores before/after
--    and the payment IDs used as proof so we can later audit the correction.
--
-- Bypass-only rows (source='bypass_only' or is_bypass_only=TRUE) intentionally
-- carry expires_at = NOW + 10y — those are NOT logged as over-issuance.

CREATE TABLE IF NOT EXISTS subscription_over_issuance_log (
    id                    SERIAL PRIMARY KEY,
    telegram_id           BIGINT NOT NULL,
    old_expires_at        TIMESTAMPTZ,
    new_expires_at        TIMESTAMPTZ NOT NULL,
    duration_added_seconds BIGINT,
    grant_action          TEXT,             -- 'renewal' | 'new_issuance' | 'upgrade' | 'admin_grant' | ...
    source                TEXT,             -- grant_access `source` param
    tariff                TEXT,
    admin_telegram_id     BIGINT,
    admin_grant_days      INTEGER,
    caller_context        TEXT,             -- stack trace snippet + free-form context
    created_at            TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
);

CREATE INDEX IF NOT EXISTS idx_over_issuance_user
    ON subscription_over_issuance_log(telegram_id);
CREATE INDEX IF NOT EXISTS idx_over_issuance_created
    ON subscription_over_issuance_log(created_at);


CREATE TABLE IF NOT EXISTS subscription_reconciliation_log (
    id                    SERIAL PRIMARY KEY,
    telegram_id           BIGINT NOT NULL,
    old_expires_at        TIMESTAMPTZ NOT NULL,
    new_expires_at        TIMESTAMPTZ NOT NULL,
    old_days_from_now     INTEGER,
    new_days_from_now     INTEGER,
    days_removed          INTEGER,
    reason                TEXT NOT NULL,
    proof_payment_ids     INTEGER[],
    total_paid_days       INTEGER,
    admin_grant_days_kept INTEGER,
    admin_telegram_id     BIGINT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
);

CREATE INDEX IF NOT EXISTS idx_recon_user
    ON subscription_reconciliation_log(telegram_id);
CREATE INDEX IF NOT EXISTS idx_recon_created
    ON subscription_reconciliation_log(created_at);
