-- Migration 023: Add paid_at to payments for audit trail
-- Purpose: Track when payment was confirmed (webhook received)
-- Safe for asyncpg (statement-by-statement execution)

ALTER TABLE payments
ADD COLUMN IF NOT EXISTS paid_at TIMESTAMP;
