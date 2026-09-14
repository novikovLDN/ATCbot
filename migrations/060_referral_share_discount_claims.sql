-- Migration 060: Referral share-discount lifetime claims
--
-- Broadcast feature: admin attaches a "Поделиться скидкой" button to a
-- broadcast. Recipient taps it → Telegram share dialog opens with their
-- personal link `t.me/<bot>?start=refd_<code>`. Whoever clicks that link
-- gets a 30% / 24h discount on basic/plus/combo tariffs.
--
-- Each telegram_id can claim this kind of discount only ONCE in their
-- lifetime. Track here, separately from `user_discounts` (which is
-- admin-managed and may be overwritten freely).

CREATE TABLE IF NOT EXISTS referral_share_discount_claims (
    telegram_id      BIGINT PRIMARY KEY,
    referrer_id      BIGINT NOT NULL,
    discount_percent INTEGER NOT NULL,
    duration_hours   INTEGER NOT NULL,
    claimed_at       TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    expires_at       TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_refdc_referrer
    ON referral_share_discount_claims(referrer_id);
