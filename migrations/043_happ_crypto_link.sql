-- Migration 043: Add happ_crypto_link column for encrypted Happ subscription links
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS happ_crypto_link TEXT;
