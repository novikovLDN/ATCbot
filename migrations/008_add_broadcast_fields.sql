-- Migration 008: Add A/B testing fields to broadcasts
-- Adds segment and A/B test fields to broadcasts table

ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS segment TEXT;
ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS is_ab_test BOOLEAN DEFAULT FALSE;
ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS message_a TEXT;
ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS message_b TEXT;

-- Add variant column to broadcast_log
ALTER TABLE broadcast_log ADD COLUMN IF NOT EXISTS variant TEXT;

