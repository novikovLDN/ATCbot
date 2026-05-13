-- 048: Cache the panel-issued subscriptionUrl + shortUuid for the BYPASS entity.
--
-- Task 2 cut-over needs the bot to surface the user's bypass subscription
-- URL alongside the premium URL right after a successful purchase.  Reading
-- it from Remnawave on every render would be wasteful: we already have a
-- mirror of the panel state in subscriptions.  Migrations 046 and 047 added
-- the equivalent caches for the premium entity (sub_url + short_uuid);
-- this one closes the symmetry for bypass.
--
-- Rollback: additive only.  Bot code reads these columns only when
-- PURCHASE_FLOW_REMNAWAVE is enabled.

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS remnawave_bypass_sub_url TEXT;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS remnawave_bypass_short_uuid TEXT;
