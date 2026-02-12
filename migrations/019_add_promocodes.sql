-- Migration 019: Promocodes system with expiration and usage limits
-- Atlas Secure: Admin promocode creation system

-- Add new columns to existing promo_codes table
DO $$
BEGIN
    -- Add duration_seconds if not exists
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'promo_codes' AND column_name = 'duration_seconds'
    ) THEN
        ALTER TABLE promo_codes ADD COLUMN duration_seconds INTEGER;
    END IF;

    -- Add expires_at if not exists
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'promo_codes' AND column_name = 'expires_at'
    ) THEN
        ALTER TABLE promo_codes ADD COLUMN expires_at TIMESTAMP;
    END IF;

    -- Add created_by if not exists
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'promo_codes' AND column_name = 'created_by'
    ) THEN
        ALTER TABLE promo_codes ADD COLUMN created_by BIGINT;
    END IF;

    -- Note: max_uses can be NULL (unlimited), so we don't add CHECK constraint
    -- The constraint will be enforced at application level

    -- Update existing promo_codes: set expires_at to NULL (unlimited) if not set
    -- This is a no-op, but kept for clarity
END $$;

-- Create index on expires_at for cleanup queries
CREATE INDEX IF NOT EXISTS idx_promocodes_expires_at ON promo_codes(expires_at) WHERE expires_at IS NOT NULL;

-- Create index on code (if not exists)
CREATE INDEX IF NOT EXISTS idx_promocodes_code ON promo_codes(UPPER(code));
