-- Migration 057: WebAuthn passkeys for admin web-dashboard.
-- One admin can register multiple credentials (iPhone Face ID + laptop
-- Touch ID + YubiKey, etc.). Each row is one platform / cross-platform
-- authenticator.

CREATE TABLE IF NOT EXISTS admin_passkeys (
    id SERIAL PRIMARY KEY,
    credential_id BYTEA NOT NULL UNIQUE,
    public_key BYTEA NOT NULL,
    sign_count BIGINT NOT NULL DEFAULT 0,
    transports TEXT,           -- JSON array, e.g. ["internal","hybrid"]
    label TEXT,                -- "iPhone 15 Pro", "MacBook" — optional
    aaguid TEXT,               -- authenticator model id
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_used_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_admin_passkeys_created_at
    ON admin_passkeys (created_at DESC);
