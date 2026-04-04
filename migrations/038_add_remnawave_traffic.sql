-- 038: Add Remnawave bypass integration (traffic limits, notifications, purchases)

-- Remnawave shortUuid stored per subscription
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS remnawave_uuid TEXT;

-- Traffic notification flags (one-shot per threshold)
ALTER TABLE users ADD COLUMN IF NOT EXISTS traffic_notified_3gb BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS traffic_notified_1gb BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS traffic_notified_500mb BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS traffic_notified_0 BOOLEAN DEFAULT FALSE;

-- Traffic pack purchases
CREATE TABLE IF NOT EXISTS traffic_purchases (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL,
    gb_amount INTEGER NOT NULL,
    price_rub INTEGER NOT NULL,
    payment_method TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_traffic_purchases_tg ON traffic_purchases(telegram_id);
