-- 050: cache the Happ Crypto Link for each migrated premium entity.
--
-- The bot now hands the user a Happ Crypto-Link form of the premium
-- subscription URL (Telegram client surfaces the user-visible URL
-- via `<code>...</code>` blocks).  Generating it requires one
-- round-trip to Remnawave's `POST /api/system/encrypt-happ-crypto-link`,
-- so we cache the result.  The cache is invalidated whenever the
-- underlying `remnawave_premium_sub_url` is rewritten (see
-- database.traffic.set_remnawave_premium_uuid_and_url).

ALTER TABLE subscriptions
  ADD COLUMN IF NOT EXISTS remnawave_premium_happ_crypto_link TEXT;
