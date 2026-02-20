-- Migration 030: Add weather system for farm game
-- Purpose: Track last good harvest for weather guarantee rule

-- Проверяем существование таблицы users перед выполнением миграции
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'users'
    ) THEN
        RAISE WARNING 'Table users does not exist, skipping migration 030';
        RETURN;
    END IF;
END $$;

-- Add farm_last_good_harvest column if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'users' 
        AND column_name = 'farm_last_good_harvest'
    ) THEN
        ALTER TABLE users ADD COLUMN farm_last_good_harvest TIMESTAMP WITH TIME ZONE;
        RAISE NOTICE 'Added farm_last_good_harvest column to users table';
    ELSE
        RAISE NOTICE 'farm_last_good_harvest column already exists in users table';
    END IF;
END $$;
