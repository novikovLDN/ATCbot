-- Migration 007: Add extended audit log fields
-- Adds VPN lifecycle audit fields to audit_log

ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS uuid TEXT;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS source TEXT;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS result TEXT CHECK (result IN ('success', 'error'));

-- Create indexes for audit log
CREATE INDEX IF NOT EXISTS idx_audit_log_uuid ON audit_log(uuid) WHERE uuid IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_log_source ON audit_log(source) WHERE source IS NOT NULL;

