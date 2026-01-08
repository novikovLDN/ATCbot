-- Migration 001: Initial schema
-- Creates core tables: users, payments, subscriptions

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    username TEXT,
    language TEXT DEFAULT 'ru',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Payments table
CREATE TABLE IF NOT EXISTS payments (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL,
    tariff TEXT NOT NULL,
    amount INTEGER,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    purchase_id TEXT
);

-- Subscriptions table
CREATE TABLE IF NOT EXISTS subscriptions (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    outline_key_id INTEGER,
    vpn_key TEXT,
    expires_at TIMESTAMP NOT NULL,
    reminder_sent BOOLEAN DEFAULT FALSE,
    reminder_3d_sent BOOLEAN DEFAULT FALSE,
    reminder_24h_sent BOOLEAN DEFAULT FALSE,
    reminder_3h_sent BOOLEAN DEFAULT FALSE,
    reminder_6h_sent BOOLEAN DEFAULT FALSE,
    admin_grant_days INTEGER DEFAULT NULL,
    auto_renew BOOLEAN DEFAULT FALSE
);

-- VPN keys table
CREATE TABLE IF NOT EXISTS vpn_keys (
    id SERIAL PRIMARY KEY,
    vpn_key TEXT UNIQUE NOT NULL,
    is_used BOOLEAN DEFAULT FALSE,
    assigned_to BIGINT,
    assigned_at TIMESTAMP
);

-- Audit log table
CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    action TEXT NOT NULL,
    telegram_id BIGINT NOT NULL,
    target_user BIGINT,
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Subscription history table
CREATE TABLE IF NOT EXISTS subscription_history (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL,
    vpn_key TEXT NOT NULL,
    start_date TIMESTAMP NOT NULL,
    end_date TIMESTAMP NOT NULL,
    action_type TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Broadcasts table
CREATE TABLE IF NOT EXISTS broadcasts (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    message TEXT,
    message_a TEXT,
    message_b TEXT,
    is_ab_test BOOLEAN DEFAULT FALSE,
    type TEXT NOT NULL,
    segment TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_by BIGINT NOT NULL
);

-- Broadcast log table
CREATE TABLE IF NOT EXISTS broadcast_log (
    id SERIAL PRIMARY KEY,
    broadcast_id INTEGER NOT NULL REFERENCES broadcasts(id) ON DELETE CASCADE,
    telegram_id BIGINT NOT NULL,
    status TEXT NOT NULL,
    variant TEXT,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Incident settings table
CREATE TABLE IF NOT EXISTS incident_settings (
    id SERIAL PRIMARY KEY,
    is_active BOOLEAN DEFAULT FALSE,
    incident_text TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- User discounts table
CREATE TABLE IF NOT EXISTS user_discounts (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    discount_percent INTEGER NOT NULL,
    expires_at TIMESTAMP NULL,
    created_by BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- VIP users table
CREATE TABLE IF NOT EXISTS vip_users (
    telegram_id BIGINT UNIQUE NOT NULL PRIMARY KEY,
    granted_by BIGINT NOT NULL,
    granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Promo codes table
CREATE TABLE IF NOT EXISTS promo_codes (
    code TEXT UNIQUE NOT NULL PRIMARY KEY,
    discount_percent INTEGER NOT NULL,
    max_uses INTEGER NULL,
    used_count INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Promo usage logs table
CREATE TABLE IF NOT EXISTS promo_usage_logs (
    id SERIAL PRIMARY KEY,
    promo_code TEXT NOT NULL,
    telegram_id BIGINT NOT NULL,
    tariff TEXT NOT NULL,
    discount_percent INTEGER NOT NULL,
    price_before INTEGER NOT NULL,
    price_after INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Initialize incident_settings with default row
INSERT INTO incident_settings (is_active, incident_text)
SELECT FALSE, NULL
WHERE NOT EXISTS (SELECT 1 FROM incident_settings);

