-- Migration 029: Add farm game columns
-- Purpose: Support farm clicker game with plots and bonus balance

-- Проверяем существование таблицы users перед выполнением миграции
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'users'
    ) THEN
        RAISE WARNING 'Table users does not exist, skipping migration 029';
        RETURN;
    END IF;
END $$;

-- Add bonus_balance column if it doesn't exist (stored in kopecks as INTEGER)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'users' 
        AND column_name = 'bonus_balance'
    ) THEN
        ALTER TABLE users ADD COLUMN bonus_balance INTEGER NOT NULL DEFAULT 0;
        RAISE NOTICE 'Added bonus_balance column to users table';
    ELSE
        RAISE NOTICE 'bonus_balance column already exists in users table';
    END IF;
END $$;

-- Add farm_plots JSONB column
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'users' 
        AND column_name = 'farm_plots'
    ) THEN
        ALTER TABLE users ADD COLUMN farm_plots JSONB DEFAULT '[]'::jsonb;
        RAISE NOTICE 'Added farm_plots column to users table';
    ELSE
        RAISE NOTICE 'farm_plots column already exists in users table';
    END IF;
END $$;

-- Add farm_plot_count INTEGER column
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'users' 
        AND column_name = 'farm_plot_count'
    ) THEN
        ALTER TABLE users ADD COLUMN farm_plot_count INTEGER DEFAULT 1;
        RAISE NOTICE 'Added farm_plot_count column to users table';
    ELSE
        RAISE NOTICE 'farm_plot_count column already exists in users table';
    END IF;
END $$;
