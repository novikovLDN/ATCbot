-- Migration 014: Add is_reachable and last_reminder_at for STAGE hardening
-- is_reachable: soft-deactivate users who blocked bot or chat not found
-- last_reminder_at: idempotency guard for reminder worker (container restart)

-- users.is_reachable: FALSE = do not send reminders/notifications (user blocked or chat gone)
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_reachable BOOLEAN DEFAULT TRUE;

-- subscriptions.last_reminder_at: timestamp of last reminder sent (idempotency)
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS last_reminder_at TIMESTAMP;
