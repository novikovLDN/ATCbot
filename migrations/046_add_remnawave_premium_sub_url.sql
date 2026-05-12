-- 046: Cache the Remnawave-issued subscription URL for the premium entity.
--
-- The subscription-URL fallback router (app/api/subscription_proxy.py) was
-- hitting GET /api/users/{uuid} on every legacy /sub/{uuid} request just to
-- learn the panel's subscriptionUrl.  That is one round-trip too many: the
-- URL is set at creation time and never changes (until the entity is
-- deleted), so we cache it next to remnawave_premium_uuid.
--
-- The migration script populates the column when it creates the entity.
-- For rows migrated before this column existed the router falls back to
-- the panel API once and back-fills the cache.

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS remnawave_premium_sub_url TEXT;
