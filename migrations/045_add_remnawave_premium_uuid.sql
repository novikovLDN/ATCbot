-- 045: Atlas → Remnawave migration — second Remnawave entity per subscription.
--
-- The legacy samopis vpnapi (master 138.124.90.195) is being retired.  Every
-- active subscription will get a SECOND Remnawave user entity assigned to the
-- "MainServer" squad (premium tier, unlimited traffic).  The existing
-- remnawave_uuid column keeps tracking the bypass tier (limited GB) and is
-- unchanged by this migration.
--
-- Columns:
--   remnawave_premium_uuid  — uuid returned by the Remnawave panel for the
--                             premium entity (may equal the legacy samopis
--                             uuid if the panel accepted the forced value).
--   samopis_migrated_at     — set once the premium entity has been provisioned
--                             so the migration script can resume safely.
--
-- Rollback: backward-compatible additive change.  Dropping the columns leaves
-- subscriptions untouched; no code path reads them unless explicitly enabled.

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS remnawave_premium_uuid TEXT;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS samopis_migrated_at TIMESTAMPTZ;

-- Lookup index for the subscription-URL fallback endpoint
-- (legacy samopis uuid → Remnawave entity).
CREATE INDEX IF NOT EXISTS idx_subscriptions_remnawave_premium_uuid
    ON subscriptions(remnawave_premium_uuid)
    WHERE remnawave_premium_uuid IS NOT NULL;
