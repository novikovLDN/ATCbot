-- Migration 032: Add subscription_type to subscriptions (basic / plus for VPN API tariff)
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS subscription_type TEXT DEFAULT 'basic';
