-- 001_sub_pairs.sql — mapping table read by the aggregator.
-- FR §2: bot populates this table; aggregator only reads (except for
-- optional invalidation triggers wired via /internal/invalidate, which is
-- also outside the write path).

CREATE TABLE IF NOT EXISTS sub_pairs (
    token         TEXT PRIMARY KEY,          -- opaque stable key in the public URL
    main_sub_url  TEXT NOT NULL,             -- full upstream subscription URL (main)
    gb_sub_url    TEXT NOT NULL,             -- full upstream subscription URL (gb)
    main_user_uuid UUID NULL,                -- for webhook invalidation
    gb_user_uuid   UUID NULL,
    status        TEXT NOT NULL DEFAULT 'active',  -- active | revoked
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

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
