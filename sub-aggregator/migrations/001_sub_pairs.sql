-- 001_sub_pairs.sql — mapping table read by the aggregator.
--
-- Bot populates this table (see bot repo migrations/079_sub_pairs.sql —
-- schemas MUST match). Aggregator only reads; writes come from the bot
-- via INSERT ON CONFLICT (telegram_id) DO UPDATE + POST /internal/invalidate.

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

CREATE UNIQUE INDEX IF NOT EXISTS sp_telegram_id_uq ON sub_pairs (telegram_id);
CREATE INDEX IF NOT EXISTS sp_main_uuid ON sub_pairs (main_user_uuid);
CREATE INDEX IF NOT EXISTS sp_gb_uuid   ON sub_pairs (gb_user_uuid);

-- Optional: dedicated read-only role for the aggregator process. Uncomment
-- and set the password out-of-band. The application then connects with
-- PG_DSN=postgres://sub_aggregator_ro:...@host/db.
--
-- CREATE ROLE sub_aggregator_ro NOLOGIN;
-- GRANT CONNECT ON DATABASE current_database() TO sub_aggregator_ro;
-- GRANT USAGE ON SCHEMA public TO sub_aggregator_ro;
-- GRANT SELECT ON sub_pairs TO sub_aggregator_ro;
-- ALTER ROLE sub_aggregator_ro LOGIN PASSWORD 'CHANGE_ME';
