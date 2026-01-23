-- Migration 004: Add pending purchases system
-- Creates pending_purchases table for purchase context hardening

-- Проверяем существование таблицы users перед выполнением миграции
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'users'
    ) THEN
        RAISE WARNING 'Table users does not exist, skipping migration 004';
        RETURN;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS pending_purchases (
    id SERIAL PRIMARY KEY,
    purchase_id TEXT UNIQUE NOT NULL,
    telegram_id BIGINT NOT NULL,
    tariff TEXT NOT NULL CHECK (tariff IN ('basic', 'plus')),
    period_days INTEGER NOT NULL,
    price_kopecks INTEGER NOT NULL,
    promo_code TEXT,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'expired')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL
);

-- Create indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_pending_purchases_status ON pending_purchases(status);
CREATE INDEX IF NOT EXISTS idx_pending_purchases_telegram_id ON pending_purchases(telegram_id);
CREATE INDEX IF NOT EXISTS idx_pending_purchases_purchase_id ON pending_purchases(purchase_id);
CREATE INDEX IF NOT EXISTS idx_pending_purchases_expires_at ON pending_purchases(expires_at);

