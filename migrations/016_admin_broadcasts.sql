-- Migration 016: Admin broadcasts audit table (no-subscription broadcasts)
CREATE TABLE IF NOT EXISTS admin_broadcasts (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    type TEXT NOT NULL,
    total_recipients INTEGER,
    success_count INTEGER,
    fail_count INTEGER
);
