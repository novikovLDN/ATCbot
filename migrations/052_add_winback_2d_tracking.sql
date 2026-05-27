-- 052: track recipients of the winback "2-day gift + 20% discount" campaign.
--
-- One column on the subscriptions row: NULL until the user has been
-- targeted by the campaign.  Set to NOW() right after the gift is
-- granted and the notification has been delivered.  Repeat runs of the
-- campaign filter on this column so the same row is never targeted twice.
--
-- Partial index keeps the candidate query fast on a table where most
-- rows already have the column populated (or are not in the eligibility
-- window at all).
--
-- Rollback: additive only.

ALTER TABLE subscriptions
  ADD COLUMN IF NOT EXISTS winback_2d_sent_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_subscriptions_winback_2d_pending
  ON subscriptions (expires_at)
  WHERE winback_2d_sent_at IS NULL
    AND status = 'expired';
