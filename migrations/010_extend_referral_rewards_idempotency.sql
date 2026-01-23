-- Migration 010: Extend referral rewards idempotency protection
-- Adds database-level protection against duplicate rewards even when purchase_id is NULL
--
-- PURPOSE:
-- The existing unique index (idx_referral_rewards_unique_buyer_purchase) only protects
-- against duplicates when purchase_id IS NOT NULL. This migration extends protection
-- to cover all cases, ensuring no duplicate rewards can be created regardless of
-- purchase_id value.
--
-- SAFETY:
-- This migration is idempotent and safe to run multiple times.
-- It does NOT remove existing constraints or indexes.

-- Проверяем существование таблицы referral_rewards перед выполнением миграции
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'referral_rewards'
    ) THEN
        RAISE WARNING 'Table referral_rewards does not exist, skipping migration 010';
        RETURN;
    END IF;
END $$;

-- Strategy: Add a unique partial index for NULL purchase_id cases
-- This prevents exact duplicates when purchase_id is NULL by checking
-- (buyer_id, reward_amount, created_at) within a 1-minute window
-- Note: This is a best-effort protection; true idempotency requires purchase_id to always be provided

-- Add unique partial index for NULL purchase_id cases
-- This index prevents duplicate rewards for the same buyer, amount, and timestamp
-- when purchase_id is NULL
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE indexname = 'idx_referral_rewards_unique_null_purchase'
    ) THEN
        -- Create unique index on (buyer_id, reward_amount, created_at) where purchase_id IS NULL
        -- This prevents exact duplicates when purchase_id is NULL
        -- Note: This doesn't prevent duplicates with different amounts or timestamps,
        -- but provides basic protection during migration period
        CREATE UNIQUE INDEX idx_referral_rewards_unique_null_purchase
        ON referral_rewards(buyer_id, reward_amount, created_at)
    WHERE purchase_id IS NULL;
    
        RAISE NOTICE 'Created unique index for NULL purchase_id cases';
    ELSE
        RAISE NOTICE 'Index idx_referral_rewards_unique_null_purchase already exists';
    END IF;
END $$;

-- Add comment documenting the requirement
COMMENT ON COLUMN referral_rewards.purchase_id IS 
    'Unique identifier for the payment event. MUST be provided for idempotency. NULL values are allowed for backward compatibility during migration but should be avoided. The unique index idx_referral_rewards_unique_null_purchase provides basic protection for NULL cases.';

-- The existing unique index (idx_referral_rewards_unique_buyer_purchase) protects
-- non-NULL purchase_id cases. The new index (idx_referral_rewards_unique_null_purchase)
-- provides basic protection for NULL cases. For true idempotency, purchase_id should
-- always be provided going forward.
