-- Migration 024: Schema hardening — TIMESTAMPTZ, UNIQUE uuid, indexes
-- Purpose: Eliminate orphan UUID risk support, enforce DB integrity
-- BILLING_SUBSCRIPTION_ENTITLEMENT_AUDIT: Critical schema fixes
--
-- 1. Convert critical timestamp columns to TIMESTAMPTZ (preserve UTC data)
-- 2. Add UNIQUE constraint on subscriptions.uuid
-- 3. Add index for expiry worker performance
--
-- Safe for asyncpg (statement-by-statement, idempotent where possible)

-- =============================================================================
-- 1. Convert TIMESTAMP to TIMESTAMPTZ (subscriptions)
-- =============================================================================

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'subscriptions' AND column_name = 'expires_at'
        AND data_type = 'timestamp without time zone'
    ) THEN
        ALTER TABLE subscriptions
        ALTER COLUMN expires_at TYPE TIMESTAMPTZ
        USING expires_at AT TIME ZONE 'UTC';
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'subscriptions' AND column_name = 'activated_at'
        AND data_type = 'timestamp without time zone'
    ) THEN
        ALTER TABLE subscriptions
        ALTER COLUMN activated_at TYPE TIMESTAMPTZ
        USING activated_at AT TIME ZONE 'UTC';
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'subscriptions' AND column_name = 'last_auto_renewal_at'
        AND data_type = 'timestamp without time zone'
    ) THEN
        ALTER TABLE subscriptions
        ALTER COLUMN last_auto_renewal_at TYPE TIMESTAMPTZ
        USING last_auto_renewal_at AT TIME ZONE 'UTC';
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'subscriptions' AND column_name = 'last_reminder_at'
        AND data_type = 'timestamp without time zone'
    ) THEN
        ALTER TABLE subscriptions
        ALTER COLUMN last_reminder_at TYPE TIMESTAMPTZ
        USING last_reminder_at AT TIME ZONE 'UTC';
    END IF;
END $$;

-- =============================================================================
-- 2. Convert TIMESTAMP to TIMESTAMPTZ (payments)
-- =============================================================================

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'payments' AND column_name = 'created_at'
        AND data_type = 'timestamp without time zone'
    ) THEN
        ALTER TABLE payments
        ALTER COLUMN created_at TYPE TIMESTAMPTZ
        USING created_at AT TIME ZONE 'UTC';
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'payments' AND column_name = 'paid_at'
        AND data_type = 'timestamp without time zone'
    ) THEN
        ALTER TABLE payments
        ALTER COLUMN paid_at TYPE TIMESTAMPTZ
        USING paid_at AT TIME ZONE 'UTC';
    END IF;
END $$;

-- =============================================================================
-- 3. Convert TIMESTAMP to TIMESTAMPTZ (pending_purchases)
-- =============================================================================

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'pending_purchases' AND column_name = 'created_at'
        AND data_type = 'timestamp without time zone'
    ) THEN
        ALTER TABLE pending_purchases
        ALTER COLUMN created_at TYPE TIMESTAMPTZ
        USING created_at AT TIME ZONE 'UTC';
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'pending_purchases' AND column_name = 'expires_at'
        AND data_type = 'timestamp without time zone'
    ) THEN
        ALTER TABLE pending_purchases
        ALTER COLUMN expires_at TYPE TIMESTAMPTZ
        USING expires_at AT TIME ZONE 'UTC';
    END IF;
END $$;

-- =============================================================================
-- 4. UNIQUE constraint on subscriptions.uuid (prevents duplicate UUID)
-- =============================================================================

CREATE UNIQUE INDEX IF NOT EXISTS idx_subscriptions_uuid_unique
ON subscriptions(uuid)
WHERE uuid IS NOT NULL;

-- =============================================================================
-- 5. Index for expiry worker performance (fast_expiry_cleanup)
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_subscriptions_active_expiry
ON subscriptions(expires_at)
WHERE status = 'active';
