-- Migration 008: Add A/B testing fields to broadcasts
-- Adds segment and A/B test fields to broadcasts table

-- Проверяем существование таблицы users перед выполнением миграции
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'users'
    ) THEN
        RAISE WARNING 'Table users does not exist, skipping migration 008';
        RETURN;
    END IF;
END $$;

ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS segment TEXT;
ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS is_ab_test BOOLEAN DEFAULT FALSE;
ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS message_a TEXT;
ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS message_b TEXT;

-- Add variant column to broadcast_log
ALTER TABLE broadcast_log ADD COLUMN IF NOT EXISTS variant TEXT;

