-- Migration 043: Admin gift links for bypass GB
--
-- Admin creates a link with (validity_days, gb_amount, max_uses).
-- Each user can redeem each link only once. When redeemed, the user
-- receives the configured GB amount via Remnawave (account is created
-- if missing).
--
-- Tables:
--   bypass_gift_links        — link definitions
--   bypass_gift_redemptions  — per-user redemption records (idempotent)

CREATE TABLE IF NOT EXISTS bypass_gift_links (
    id            SERIAL PRIMARY KEY,
    code          TEXT NOT NULL UNIQUE,
    created_by    BIGINT NOT NULL,
    gb_amount     INTEGER NOT NULL CHECK (gb_amount > 0),
    validity_days INTEGER NOT NULL CHECK (validity_days > 0),
    max_uses      INTEGER NOT NULL CHECK (max_uses > 0),
    created_at    TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    expires_at    TIMESTAMP NOT NULL,
    deleted_at    TIMESTAMP NULL
);

CREATE INDEX IF NOT EXISTS idx_bypass_gift_links_code ON bypass_gift_links(code);
CREATE INDEX IF NOT EXISTS idx_bypass_gift_links_created_by ON bypass_gift_links(created_by);
CREATE INDEX IF NOT EXISTS idx_bypass_gift_links_active
    ON bypass_gift_links(expires_at) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS bypass_gift_redemptions (
    id           SERIAL PRIMARY KEY,
    link_id      INTEGER NOT NULL REFERENCES bypass_gift_links(id) ON DELETE CASCADE,
    telegram_id  BIGINT NOT NULL,
    redeemed_at  TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    gb_granted   INTEGER NOT NULL,
    UNIQUE (link_id, telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_bypass_gift_redemptions_link
    ON bypass_gift_redemptions(link_id);
CREATE INDEX IF NOT EXISTS idx_bypass_gift_redemptions_user
    ON bypass_gift_redemptions(telegram_id);
