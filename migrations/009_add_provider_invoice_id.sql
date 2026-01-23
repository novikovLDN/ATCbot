-- Migration 009: Add provider_invoice_id column to pending_purchases table
-- This column stores the payment provider's invoice ID (e.g., CryptoBot invoice_id)
-- NULL for non-cryptobot purchases, TEXT for cryptobot invoice IDs

-- Проверяем существование таблицы users перед выполнением миграции
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'users'
    ) THEN
        RAISE WARNING 'Table users does not exist, skipping migration 009';
        RETURN;
    END IF;
END $$;

-- Add column if it doesn't exist (idempotent)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'pending_purchases' 
        AND column_name = 'provider_invoice_id'
    ) THEN
        ALTER TABLE pending_purchases ADD COLUMN provider_invoice_id TEXT;
    END IF;
END $$;
