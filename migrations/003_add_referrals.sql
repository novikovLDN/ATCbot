-- Migration 003: Add referral system
-- Adds referral fields to users and creates referrals table
-- 
-- КРИТИЧНО: Эта миграция зависит от migration 001 (таблица users должна существовать)
-- Если users не существует, миграция безопасно пропустит все операции с users
-- но создаст referrals (независимая таблица)

-- Проверяем существование таблицы users перед любыми операциями
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'users'
    ) THEN
        RAISE WARNING 'Table users does not exist, skipping referral migration for users table';
        -- НЕ RETURN - продолжаем создавать таблицу referrals (независимая)
    ELSE
        -- Add referral fields to users (только если таблица существует)
        ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code TEXT;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS referrer_id BIGINT;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_level TEXT DEFAULT 'base' CHECK (referral_level IN ('base', 'vip'));
        
        -- Migrate data from referred_by to referrer_id if needed
        IF EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'users' 
            AND column_name = 'referred_by'
        ) THEN
            UPDATE users 
            SET referrer_id = referred_by 
            WHERE referrer_id IS NULL AND referred_by IS NOT NULL;
        END IF;
        
        -- Create unique index on referral_code (partial index for non-null values)
        -- Только если таблица users существует
        IF NOT EXISTS (
            SELECT 1 FROM pg_indexes 
            WHERE indexname = 'idx_users_referral_code'
        ) THEN
            CREATE UNIQUE INDEX idx_users_referral_code 
            ON users(referral_code) 
            WHERE referral_code IS NOT NULL;
        END IF;
        
        -- Create index on referrer_id (partial index for non-null values)
        IF NOT EXISTS (
            SELECT 1 FROM pg_indexes 
            WHERE indexname = 'idx_users_referrer_id'
        ) THEN
            CREATE INDEX idx_users_referrer_id 
            ON users(referrer_id) 
            WHERE referrer_id IS NOT NULL;
        END IF;
    END IF;
END $$;

-- Referrals table (независимая таблица, создаем всегда)
CREATE TABLE IF NOT EXISTS referrals (
    id SERIAL PRIMARY KEY,
    referrer_user_id BIGINT NOT NULL,
    referred_user_id BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_rewarded BOOLEAN DEFAULT FALSE,
    reward_amount INTEGER DEFAULT 0,
    UNIQUE (referred_user_id)
);

-- Migrate old column names if they exist (только если таблица referrals существует)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'referrals'
    ) THEN
        -- Rename referrer_id to referrer_user_id if old column exists
        IF EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'referrals' 
            AND column_name = 'referrer_id'
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'referrals' 
            AND column_name = 'referrer_user_id'
        ) THEN
            ALTER TABLE referrals RENAME COLUMN referrer_id TO referrer_user_id;
        END IF;
        
        -- Rename referred_id to referred_user_id if old column exists
        IF EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'referrals' 
            AND column_name = 'referred_id'
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'referrals' 
            AND column_name = 'referred_user_id'
        ) THEN
            ALTER TABLE referrals RENAME COLUMN referred_id TO referred_user_id;
        END IF;
        
        -- Rename rewarded to is_rewarded if old column exists
        IF EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'referrals' 
            AND column_name = 'rewarded'
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'referrals' 
            AND column_name = 'is_rewarded'
        ) THEN
            ALTER TABLE referrals RENAME COLUMN rewarded TO is_rewarded;
        END IF;
        
        -- Add reward_amount if missing
        ALTER TABLE referrals ADD COLUMN IF NOT EXISTS reward_amount INTEGER DEFAULT 0;
        
        -- Create index on referrer_user_id (только если не существует)
        IF NOT EXISTS (
            SELECT 1 FROM pg_indexes 
            WHERE indexname = 'idx_referrals_referrer'
        ) THEN
            CREATE INDEX idx_referrals_referrer ON referrals(referrer_user_id);
        END IF;
    END IF;
END $$;

