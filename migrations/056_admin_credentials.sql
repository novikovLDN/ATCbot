-- Migration 056: admin web-dashboard credentials.
-- One row global — the bot has a single admin (config.ADMIN_TELEGRAM_ID).

CREATE TABLE IF NOT EXISTS admin_credentials (
    id SMALLSERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
