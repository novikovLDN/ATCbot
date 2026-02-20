-- Migration 028: Ensure purchase_id column exists in payments table
-- Purpose: Fix missing purchase_id column error in finalize_purchase()
-- The column should exist from migration 001, but this ensures it's present

-- Проверяем существование таблицы users перед выполнением миграции
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'payments'
    ) THEN
        RAISE WARNING 'Table payments does not exist, skipping migration 028';
        RETURN;
    END IF;
END $$;

-- Add column if it doesn't exist (idempotent)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'payments' 
        AND column_name = 'purchase_id'
    ) THEN
        ALTER TABLE payments ADD COLUMN purchase_id TEXT;
        RAISE NOTICE 'Added purchase_id column to payments table';
    ELSE
        RAISE NOTICE 'purchase_id column already exists in payments table';
    END IF;
END $$;
