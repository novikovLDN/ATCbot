-- Migration 025: Full TIMESTAMPTZ alignment
-- Converts ALL remaining TIMESTAMP columns to TIMESTAMPTZ
-- Assumes existing data is stored as UTC (AT TIME ZONE 'UTC')
-- Safe for asyncpg, backward compatible, preserves data
-- See docs/MIGRATION_TIMESTAMPTZ_ALIGNMENT.md

-- users
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='users' AND column_name='created_at' AND data_type='timestamp without time zone') THEN
        ALTER TABLE users ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';
    END IF;
END $$;

-- vpn_keys
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='vpn_keys' AND column_name='assigned_at' AND data_type='timestamp without time zone') THEN
        ALTER TABLE vpn_keys ALTER COLUMN assigned_at TYPE TIMESTAMPTZ USING assigned_at AT TIME ZONE 'UTC';
    END IF;
END $$;

-- audit_log
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='audit_log' AND column_name='created_at' AND data_type='timestamp without time zone') THEN
        ALTER TABLE audit_log ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';
    END IF;
END $$;

-- subscription_history
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='subscription_history' AND column_name='start_date' AND data_type='timestamp without time zone') THEN
        ALTER TABLE subscription_history ALTER COLUMN start_date TYPE TIMESTAMPTZ USING start_date AT TIME ZONE 'UTC';
    END IF;
END $$;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='subscription_history' AND column_name='end_date' AND data_type='timestamp without time zone') THEN
        ALTER TABLE subscription_history ALTER COLUMN end_date TYPE TIMESTAMPTZ USING end_date AT TIME ZONE 'UTC';
    END IF;
END $$;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='subscription_history' AND column_name='created_at' AND data_type='timestamp without time zone') THEN
        ALTER TABLE subscription_history ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';
    END IF;
END $$;

-- broadcasts
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='broadcasts' AND column_name='created_at' AND data_type='timestamp without time zone') THEN
        ALTER TABLE broadcasts ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';
    END IF;
END $$;

-- broadcast_log
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='broadcast_log' AND column_name='sent_at' AND data_type='timestamp without time zone') THEN
        ALTER TABLE broadcast_log ALTER COLUMN sent_at TYPE TIMESTAMPTZ USING sent_at AT TIME ZONE 'UTC';
    END IF;
END $$;

-- incident_settings
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='incident_settings' AND column_name='updated_at' AND data_type='timestamp without time zone') THEN
        ALTER TABLE incident_settings ALTER COLUMN updated_at TYPE TIMESTAMPTZ USING updated_at AT TIME ZONE 'UTC';
    END IF;
END $$;

-- user_discounts
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='user_discounts' AND column_name='expires_at' AND data_type='timestamp without time zone') THEN
        ALTER TABLE user_discounts ALTER COLUMN expires_at TYPE TIMESTAMPTZ USING expires_at AT TIME ZONE 'UTC';
    END IF;
END $$;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='user_discounts' AND column_name='created_at' AND data_type='timestamp without time zone') THEN
        ALTER TABLE user_discounts ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';
    END IF;
END $$;

-- vip_users
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='vip_users' AND column_name='granted_at' AND data_type='timestamp without time zone') THEN
        ALTER TABLE vip_users ALTER COLUMN granted_at TYPE TIMESTAMPTZ USING granted_at AT TIME ZONE 'UTC';
    END IF;
END $$;

-- promo_codes
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='promo_codes' AND column_name='created_at' AND data_type='timestamp without time zone') THEN
        ALTER TABLE promo_codes ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';
    END IF;
END $$;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='promo_codes' AND column_name='expires_at' AND data_type='timestamp without time zone') THEN
        ALTER TABLE promo_codes ALTER COLUMN expires_at TYPE TIMESTAMPTZ USING expires_at AT TIME ZONE 'UTC';
    END IF;
END $$;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='promo_codes' AND column_name='deleted_at' AND data_type='timestamp without time zone') THEN
        ALTER TABLE promo_codes ALTER COLUMN deleted_at TYPE TIMESTAMPTZ USING deleted_at AT TIME ZONE 'UTC';
    END IF;
END $$;

-- promo_usage_logs
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='promo_usage_logs' AND column_name='created_at' AND data_type='timestamp without time zone') THEN
        ALTER TABLE promo_usage_logs ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';
    END IF;
END $$;

-- referral_rewards
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='referral_rewards' AND column_name='created_at' AND data_type='timestamp without time zone') THEN
        ALTER TABLE referral_rewards ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';
    END IF;
END $$;

-- balance_transactions
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='balance_transactions' AND column_name='created_at' AND data_type='timestamp without time zone') THEN
        ALTER TABLE balance_transactions ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';
    END IF;
END $$;

-- withdrawal_requests
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='withdrawal_requests' AND column_name='created_at' AND data_type='timestamp without time zone') THEN
        ALTER TABLE withdrawal_requests ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';
    END IF;
END $$;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='withdrawal_requests' AND column_name='processed_at' AND data_type='timestamp without time zone') THEN
        ALTER TABLE withdrawal_requests ALTER COLUMN processed_at TYPE TIMESTAMPTZ USING processed_at AT TIME ZONE 'UTC';
    END IF;
END $$;

-- admin_broadcasts
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='admin_broadcasts' AND column_name='created_at' AND data_type='timestamp without time zone') THEN
        ALTER TABLE admin_broadcasts ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';
    END IF;
END $$;

-- subscriptions (remaining columns from 006)
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='subscriptions' AND column_name='last_notification_sent_at' AND data_type='timestamp without time zone') THEN
        ALTER TABLE subscriptions ALTER COLUMN last_notification_sent_at TYPE TIMESTAMPTZ USING last_notification_sent_at AT TIME ZONE 'UTC';
    END IF;
END $$;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='subscriptions' AND column_name='first_traffic_at' AND data_type='timestamp without time zone') THEN
        ALTER TABLE subscriptions ALTER COLUMN first_traffic_at TYPE TIMESTAMPTZ USING first_traffic_at AT TIME ZONE 'UTC';
    END IF;
END $$;
