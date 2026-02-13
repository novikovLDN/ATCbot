-- Migration 020: Promocode system CHECK constraints
-- Atlas Secure: Add data integrity constraints for promocodes

-- Add CHECK constraint for used_count >= 0
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conname = 'promocodes_used_count_non_negative'
        AND conrelid = 'promo_codes'::regclass
    ) THEN
        ALTER TABLE promo_codes 
        ADD CONSTRAINT promocodes_used_count_non_negative 
        CHECK (used_count >= 0);
    END IF;
END $$;

-- Add CHECK constraint for max_uses > 0 (when not NULL)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conname = 'promocodes_max_uses_positive'
        AND conrelid = 'promo_codes'::regclass
    ) THEN
        ALTER TABLE promo_codes 
        ADD CONSTRAINT promocodes_max_uses_positive 
        CHECK (max_uses IS NULL OR max_uses > 0);
    END IF;
END $$;

-- Add CHECK constraint for used_count <= max_uses (when max_uses is not NULL)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conname = 'promocodes_used_not_exceed_max'
        AND conrelid = 'promo_codes'::regclass
    ) THEN
        ALTER TABLE promo_codes 
        ADD CONSTRAINT promocodes_used_not_exceed_max 
        CHECK (max_uses IS NULL OR used_count <= max_uses);
    END IF;
END $$;
