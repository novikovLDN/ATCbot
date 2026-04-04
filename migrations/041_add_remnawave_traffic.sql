-- Migration 041: Remnawave (Yandex node) traffic tracking
-- Adds: remnawave_uuid to subscriptions, traffic notification flags to users,
--        traffic_purchases table for purchased GB packs.

-- Store Remnawave user UUID (same as Xray uuid, stored separately for clarity)
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS remnawave_uuid TEXT;

-- Traffic notification flags (to avoid spamming)
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
    purchase_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_traffic_purchases_telegram_id
    ON traffic_purchases (telegram_id);
