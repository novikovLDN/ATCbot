-- Migration 036: Complete notification system overhaul
-- New reminder flags for redesigned notification schedule
-- Referral cashback multiplier support

-- New paid subscription reminder flags (7d, 1d)
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS reminder_7d_sent BOOLEAN DEFAULT FALSE;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS reminder_1d_sent BOOLEAN DEFAULT FALSE;

-- Trial notification redesign: 24h and 3h before expiry
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_notif_24h_sent BOOLEAN DEFAULT FALSE;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_notif_3h_sent BOOLEAN DEFAULT FALSE;

-- Cashback multiplier for referral promotions
CREATE TABLE IF NOT EXISTS cashback_promotions (
    id SERIAL PRIMARY KEY,
    multiplier INTEGER NOT NULL DEFAULT 2,
    starts_at TIMESTAMP NOT NULL,
    ends_at TIMESTAMP NOT NULL,
    created_by BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    is_active BOOLEAN DEFAULT TRUE
);

-- User-level cashback multiplier activation
CREATE TABLE IF NOT EXISTS user_cashback_multipliers (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL,
    multiplier INTEGER NOT NULL DEFAULT 2,
    promo_id INTEGER REFERENCES cashback_promotions(id),
    starts_at TIMESTAMP NOT NULL,
    ends_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_cashback_mult_tgid ON user_cashback_multipliers(telegram_id);
CREATE INDEX IF NOT EXISTS idx_user_cashback_mult_active ON user_cashback_multipliers(ends_at);

-- Admin notification templates storage
CREATE TABLE IF NOT EXISTS admin_notification_templates (
    id SERIAL PRIMARY KEY,
    category TEXT NOT NULL,  -- 'promo', 'retention', 'engagement', 'reactivation'
    template_key TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    discount_percent INTEGER DEFAULT 0,
    requires_period BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Sent admin notifications log
CREATE TABLE IF NOT EXISTS admin_notification_log (
    id SERIAL PRIMARY KEY,
    template_key TEXT,
    category TEXT,
    segment TEXT,
    discount_percent INTEGER DEFAULT 0,
    period_days INTEGER,
    sent_by BIGINT NOT NULL,
    total_sent INTEGER DEFAULT 0,
    total_failed INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);
