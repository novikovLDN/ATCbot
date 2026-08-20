-- 079_sub_pairs.sql — mapping table for the sub-aggregator service.
--
-- Bot writes rows here (one per user who's on the aggregator);
-- sub-aggregator service (separate process) reads them to serve
-- https://<SUB_DOMAIN>/<token> requests.
--
-- Rollout is gated by config.SUB_AGGREGATOR_ADMIN_ONLY (bool). When true
-- only ADMIN_TELEGRAM_ID gets rows; when false every subscription creates
-- a row on the next mutation (renew/create).

CREATE TABLE IF NOT EXISTS sub_pairs (
    token          TEXT PRIMARY KEY,
    telegram_id    BIGINT NOT NULL,
    main_sub_url   TEXT NOT NULL,
    gb_sub_url     TEXT NOT NULL,
    main_user_uuid UUID NULL,
    gb_user_uuid   UUID NULL,
    status         TEXT NOT NULL DEFAULT 'active',   -- active | revoked
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One aggregator token per user — repeat calls upsert instead of proliferating.
CREATE UNIQUE INDEX IF NOT EXISTS sp_telegram_id_uq ON sub_pairs (telegram_id);

CREATE INDEX IF NOT EXISTS sp_main_uuid ON sub_pairs (main_user_uuid);
CREATE INDEX IF NOT EXISTS sp_gb_uuid   ON sub_pairs (gb_user_uuid);
