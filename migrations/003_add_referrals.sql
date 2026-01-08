-- Migration 003: Add referral system
-- Adds referral fields to users and creates referrals table

-- Add referral fields to users
ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS referrer_id BIGINT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_level TEXT DEFAULT 'base' CHECK (referral_level IN ('base', 'vip'));

-- Migrate data from referred_by to referrer_id if needed
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'users' 
        AND column_name = 'referred_by'
    ) THEN
        UPDATE users 
        SET referrer_id = referred_by 
        WHERE referrer_id IS NULL AND referred_by IS NOT NULL;
    END IF;
END $$;

-- Create unique index on referral_code (partial index for non-null values)
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_referral_code 
ON users(referral_code) 
WHERE referral_code IS NOT NULL;

-- Create index on referrer_id (partial index for non-null values)
CREATE INDEX IF NOT EXISTS idx_users_referrer_id 
ON users(referrer_id) 
WHERE referrer_id IS NOT NULL;

-- Referrals table
CREATE TABLE IF NOT EXISTS referrals (
    id SERIAL PRIMARY KEY,
    referrer_user_id BIGINT NOT NULL,
    referred_user_id BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_rewarded BOOLEAN DEFAULT FALSE,
    reward_amount INTEGER DEFAULT 0,
    UNIQUE (referred_user_id)
);

-- Migrate old column names if they exist
DO $$
BEGIN
    -- Rename referrer_id to referrer_user_id if old column exists
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'referrals' 
        AND column_name = 'referrer_id'
        AND column_name != 'referrer_user_id'
    ) THEN
        ALTER TABLE referrals RENAME COLUMN referrer_id TO referrer_user_id;
    END IF;
    
    -- Rename referred_id to referred_user_id if old column exists
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'referrals' 
        AND column_name = 'referred_id'
        AND column_name != 'referred_user_id'
    ) THEN
        ALTER TABLE referrals RENAME COLUMN referred_id TO referred_user_id;
    END IF;
    
    -- Rename rewarded to is_rewarded if old column exists
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'referrals' 
        AND column_name = 'rewarded'
        AND column_name != 'is_rewarded'
    ) THEN
        ALTER TABLE referrals RENAME COLUMN rewarded TO is_rewarded;
    END IF;
END $$;

-- Add reward_amount if missing
ALTER TABLE referrals ADD COLUMN IF NOT EXISTS reward_amount INTEGER DEFAULT 0;

-- Create index on referrer_user_id
CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_user_id);

