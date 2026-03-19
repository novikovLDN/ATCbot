-- Migration 039: Composite indexes for 500K+ users scalability
-- These indexes cover the most frequent query patterns in handlers and workers

-- Subscriptions: active subscription lookup by user (handlers, auto_renewal, reminders)
CREATE INDEX IF NOT EXISTS idx_subscriptions_telegram_id_status
    ON subscriptions(telegram_id, status);

-- Subscriptions: expiry-based batch queries (workers: fast_expiry, auto_renewal, reminders)
CREATE INDEX IF NOT EXISTS idx_subscriptions_expires_at_status
    ON subscriptions(expires_at, status)
    WHERE status = 'active';

-- Subscriptions: auto-renewal candidate lookup (auto_renewal worker)
CREATE INDEX IF NOT EXISTS idx_subscriptions_auto_renew_active
    ON subscriptions(auto_renew, expires_at)
    WHERE status = 'active' AND auto_renew = TRUE;

-- Subscriptions: trial source filtering (trial_notifications worker)
CREATE INDEX IF NOT EXISTS idx_subscriptions_source_status_expires
    ON subscriptions(source, status, expires_at)
    WHERE source = 'trial' AND status = 'active';

-- Subscriptions: activation_status for pending activations (activation_worker)
CREATE INDEX IF NOT EXISTS idx_subscriptions_activation_status
    ON subscriptions(activation_status)
    WHERE activation_status = 'pending';

-- Payments: pending payment lookup by user (purchase flow, handlers)
CREATE INDEX IF NOT EXISTS idx_payments_telegram_id_status
    ON payments(telegram_id, status);

-- Payments: approved payments lookup (auto_renewal, statistics)
CREATE INDEX IF NOT EXISTS idx_payments_status_created
    ON payments(status, created_at)
    WHERE status = 'approved';

-- Users: reachability filter for worker batch queries
CREATE INDEX IF NOT EXISTS idx_users_is_reachable
    ON users(is_reachable)
    WHERE is_reachable = FALSE;

-- Pending purchases: composite for active purchase lookup
CREATE INDEX IF NOT EXISTS idx_pending_purchases_telegram_status_expires
    ON pending_purchases(telegram_id, status, expires_at)
    WHERE status = 'pending';
