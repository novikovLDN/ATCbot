-- Migration 052: Farm storm mechanic
--
-- Adds the periodic "Storm" event for the Farm game. A storm hits every
-- 7–10 days (random), announced 24h ahead. Growing plots without a
-- shield die or auto-harvest at 50% for offline users.
--
-- INVARIANT (existing users): farm_plot_count is NEVER touched.
-- INVARIANT (running plants):  farm_plots elements are amended with a
--   new key storm_shielded=false; existing data is preserved.
--
-- Adds:
--   - users.last_seen_at        — for online/offline detection on storm execution
--   - users.farm_plots[*].storm_shielded — bool flag, default false
--   - farm_storms table         — schedule + audit of storms
--   - one row in farm_storms    — first storm 14 days after this migration runs
--   - pending_purchases CHECK constraints extended for purchase_type='farm_effect'
--     and tariff='farm_storm_shield'

-- ── 1. users.last_seen_at ─────────────────────────────────────────────
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP;


-- ── 2. Patch existing farm_plots with storm_shielded=false ────────────
-- jsonb_set on each array element via a SELECT … FROM jsonb_array_elements.
-- Skips users with empty/null farm_plots.  Idempotent: only adds the key if
-- absent, so re-running the migration does not stomp a previously set flag.
UPDATE users u
SET farm_plots = (
    SELECT jsonb_agg(
        CASE
            WHEN plot ? 'storm_shielded' THEN plot
            ELSE plot || jsonb_build_object('storm_shielded', false)
        END
    )
    FROM jsonb_array_elements(u.farm_plots) AS plot
)
WHERE u.farm_plots IS NOT NULL
  AND jsonb_typeof(u.farm_plots) = 'array'
  AND jsonb_array_length(u.farm_plots) > 0;


-- ── 3. farm_storms — schedule + audit table ───────────────────────────
CREATE TABLE IF NOT EXISTS farm_storms (
    id                    SERIAL PRIMARY KEY,
    scheduled_at          TIMESTAMP NOT NULL,
    announced_at          TIMESTAMP,
    executed_at           TIMESTAMP,
    killed_count          INTEGER NOT NULL DEFAULT 0,
    shielded_count        INTEGER NOT NULL DEFAULT 0,
    auto_harvested_count  INTEGER NOT NULL DEFAULT 0,
    auto_harvested_rub    INTEGER NOT NULL DEFAULT 0,
    created_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_farm_storms_scheduled_at
    ON farm_storms (scheduled_at);

-- Partial unique index: only ONE storm may be "pending" (no executed_at) at a time.
-- Prevents accidental double-schedule races.
CREATE UNIQUE INDEX IF NOT EXISTS idx_farm_storms_one_pending
    ON farm_storms ((1)) WHERE executed_at IS NULL;


-- ── 4. Schedule the first storm 14 days out ───────────────────────────
-- Buffer so existing users see the new UI before anything dies.
-- Only inserts if no pending storm exists (idempotent re-runs).
INSERT INTO farm_storms (scheduled_at)
SELECT CURRENT_TIMESTAMP + INTERVAL '14 days'
WHERE NOT EXISTS (
    SELECT 1 FROM farm_storms WHERE executed_at IS NULL
);


-- ── 5. Extend pending_purchases CHECK constraints ─────────────────────
-- purchase_type='farm_effect', tariff='farm_storm_shield'.
-- NOT VALID so the migration never fails on legacy rows.
ALTER TABLE pending_purchases DROP CONSTRAINT IF EXISTS pending_purchases_purchase_type_check;
ALTER TABLE pending_purchases ADD CONSTRAINT pending_purchases_purchase_type_check
    CHECK (purchase_type IN (
        'subscription', 'balance_topup', 'gift', 'telegram_premium',
        'telegram_stars', 'traffic_pack', 'apple_id', 'steam', 'proxy',
        'farm_effect'
    )) NOT VALID;

ALTER TABLE pending_purchases DROP CONSTRAINT IF EXISTS pending_purchases_tariff_check;
ALTER TABLE pending_purchases ADD CONSTRAINT pending_purchases_tariff_check
    CHECK (
        tariff IS NULL
        OR tariff IN (
            'basic', 'plus',
            'biz_starter', 'biz_team', 'biz_business', 'biz_pro', 'biz_enterprise', 'biz_ultimate',
            'telegram_premium', 'telegram_stars', 'proxy',
            'farm_storm_shield'
        )
        OR tariff LIKE 'traffic_%'
        OR tariff LIKE 'apple_id_%'
        OR tariff LIKE 'bypass_%'
        OR tariff LIKE 'steam_%'
    ) NOT VALID;
