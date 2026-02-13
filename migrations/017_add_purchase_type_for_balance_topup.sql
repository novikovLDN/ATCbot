-- Migration 017: Add purchase_type for clean separation of subscription vs balance_topup
-- Enables balance top-up without passing period_days=0 through subscription logic

ALTER TABLE pending_purchases ADD COLUMN IF NOT EXISTS purchase_type TEXT DEFAULT 'subscription';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pending_purchases_purchase_type_check') THEN
        ALTER TABLE pending_purchases ADD CONSTRAINT pending_purchases_purchase_type_check
            CHECK (purchase_type IN ('subscription', 'balance_topup'));
    END IF;
END $$;

ALTER TABLE pending_purchases ALTER COLUMN tariff DROP NOT NULL;
ALTER TABLE pending_purchases ALTER COLUMN period_days DROP NOT NULL;

-- Drop old tariff CHECK (name may vary by PostgreSQL version), add new one allowing NULL
DO $$
DECLARE
    cname TEXT;
BEGIN
    SELECT con.conname INTO cname FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        WHERE rel.relname = 'pending_purchases' AND con.contype = 'c'
        AND pg_get_constraintdef(con.oid) LIKE '%tariff%' LIMIT 1;
    IF cname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE pending_purchases DROP CONSTRAINT %I', cname);
    END IF;
    ALTER TABLE pending_purchases ADD CONSTRAINT pending_purchases_tariff_check
        CHECK (tariff IS NULL OR tariff IN ('basic', 'plus'));
END $$;
