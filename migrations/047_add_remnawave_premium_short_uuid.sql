-- 047: Cache the panel-issued shortUuid for the premium entity.
--
-- Remnawave v2.7+ separates the user entity into three identifier fields:
--   uuid        — internal panel ID (used for /api/users/{uuid})
--   vlessUuid   — the UUID embedded in VLESS connection strings; this is
--                 what we force during migration so legacy samopis links
--                 keep working on the new inbounds
--   shortUuid   — short ID used to build the subscription URL
--                 (/api/sub/{shortUuid})
--
-- Migration 046 cached the full subscriptionUrl.  This adds the shortUuid
-- alongside it so URL re-construction is possible if the panel rotates
-- the subscriptionUrl (e.g. via domain change) without re-running the
-- migration.

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS remnawave_premium_short_uuid TEXT;
