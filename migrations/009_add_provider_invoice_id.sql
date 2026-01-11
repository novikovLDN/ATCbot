-- Migration 009: Add provider_invoice_id column to pending_purchases table
-- This column stores the payment provider's invoice ID (e.g., CryptoBot invoice_id)
-- NULL for non-cryptobot purchases, TEXT for cryptobot invoice IDs

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
