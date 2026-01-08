-- Migration 005: Add referral rewards system
-- Creates referral_rewards table for tracking cashback accruals

CREATE TABLE IF NOT EXISTS referral_rewards (
    id SERIAL PRIMARY KEY,
    referrer_id BIGINT NOT NULL,
    buyer_id BIGINT NOT NULL,
    purchase_id TEXT,
    purchase_amount INTEGER NOT NULL,
    percent INTEGER NOT NULL,
    reward_amount INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create unique partial index to prevent duplicate rewards for same purchase_id
CREATE UNIQUE INDEX IF NOT EXISTS idx_referral_rewards_unique_buyer_purchase 
ON referral_rewards(buyer_id, purchase_id) 
WHERE purchase_id IS NOT NULL;

-- Create indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_referral_rewards_referrer ON referral_rewards(referrer_id);
CREATE INDEX IF NOT EXISTS idx_referral_rewards_buyer ON referral_rewards(buyer_id);
CREATE INDEX IF NOT EXISTS idx_referral_rewards_purchase_id 
ON referral_rewards(purchase_id) 
WHERE purchase_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_referral_rewards_created_at ON referral_rewards(created_at);

