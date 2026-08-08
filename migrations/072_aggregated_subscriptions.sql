-- 072: aggregated_subscriptions (admin-only beta feature)
-- Хранит связку combined_token → telegram_id + оба remnawave UUID
-- (premium + bypass/whitelist). Используется FastAPI-роутом
-- /agg/{combined_token} для merge двух подписок в одну ссылку.
--
-- Rollback: DROP TABLE aggregated_subscriptions; либо AGG_ENABLED=false —
-- роут не монтируется, таблица просто существует и не мешает.

CREATE TABLE IF NOT EXISTS aggregated_subscriptions (
    combined_token VARCHAR(64) PRIMARY KEY,
    telegram_id    BIGINT      NOT NULL,
    premium_uuid   VARCHAR(64) NOT NULL,
    whitelist_uuid VARCHAR(64) NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_accessed  TIMESTAMPTZ,
    access_count   INTEGER     NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_aggsub_tg
    ON aggregated_subscriptions(telegram_id);
