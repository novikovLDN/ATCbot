-- Add remnawave_short_uuid column for subscription URLs
-- remnawave_uuid stores the full UUID (for API calls: /api/users/{uuid})
-- remnawave_short_uuid stores the short UUID (for subscription URLs: /api/sub/{shortUuid})
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS remnawave_short_uuid TEXT;
