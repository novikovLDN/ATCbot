-- Migration 027: Add dice_last_played column for separate dice game cooldown
-- Bowling uses game_last_played, Dice uses dice_last_played (separate cooldowns)

-- Проверяем существование таблицы users перед выполнением миграции
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'users'
    ) THEN
        RAISE WARNING 'Table users does not exist, skipping migration 027';
        RETURN;
    END IF;
END $$;

-- Add column if it doesn't exist (idempotent)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'users' 
        AND column_name = 'dice_last_played'
    ) THEN
        ALTER TABLE users ADD COLUMN dice_last_played TIMESTAMPTZ;
    END IF;
END $$;
