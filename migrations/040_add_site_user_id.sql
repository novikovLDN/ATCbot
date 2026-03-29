-- Migration 040: Add site_user_id to users table for website sync
-- Links telegram user to Atlas Secure website account
-- Rollback safe: column is optional, old code ignores it

ALTER TABLE users ADD COLUMN IF NOT EXISTS site_user_id TEXT;

-- Index for fast lookup by site_user_id
CREATE INDEX IF NOT EXISTS idx_users_site_user_id ON users (site_user_id) WHERE site_user_id IS NOT NULL;
