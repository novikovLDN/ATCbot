-- 049: track recipients of the Task-3 migration broadcast.
--
-- One column per subscription; NULL until the migration notice has been
-- delivered (response was 2xx).  Background re-runs of the broadcast
-- helper filter on this column so a re-trigger from the admin dashboard
-- never double-sends.
--
-- A partial index keeps the candidate query fast on a table where most
-- rows already have the column populated.

ALTER TABLE subscriptions
  ADD COLUMN IF NOT EXISTS migration_notice_sent_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_subscriptions_migration_notice_pending
  ON subscriptions (telegram_id)
  WHERE migration_notice_sent_at IS NULL
    AND remnawave_premium_uuid IS NOT NULL
    AND status = 'active';
