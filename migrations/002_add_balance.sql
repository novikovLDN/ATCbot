-- Migration 002: Add balance system
-- Adds balance column to users and creates balance_transactions table

-- Add balance column to users (stored in kopecks as INTEGER)
ALTER TABLE users ADD COLUMN IF NOT EXISTS balance INTEGER NOT NULL DEFAULT 0;

-- Balance transactions table
CREATE TABLE IF NOT EXISTS balance_transactions (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    amount NUMERIC NOT NULL,
    type TEXT NOT NULL,
    source TEXT,
    description TEXT,
    related_user_id BIGINT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Add related_user_id column if missing
ALTER TABLE balance_transactions ADD COLUMN IF NOT EXISTS related_user_id BIGINT;

-- Add source column if missing
ALTER TABLE balance_transactions ADD COLUMN IF NOT EXISTS source TEXT;

-- Ensure amount is NUMERIC type
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'balance_transactions' 
        AND column_name = 'amount' 
        AND data_type != 'numeric'
    ) THEN
        ALTER TABLE balance_transactions ALTER COLUMN amount TYPE NUMERIC USING amount::NUMERIC;
    END IF;
END $$;

