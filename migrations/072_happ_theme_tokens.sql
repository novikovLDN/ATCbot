-- 072: happ_theme_tokens (admin-only Happ custom HTML theme feature)
-- Токен → telegram_id + remnawave_uuid; endpoint /happ-theme/{token}
-- по User-Agent отдаёт красивую HTML-страницу с тёмной темой или
-- проксирует raw subscription с Remnawave. Никого не трогает — только
-- админ создаёт токены через /happ_theme.
--
-- Rollback: DROP TABLE happ_theme_tokens; либо HAPP_THEME_ENABLED=false.

CREATE TABLE IF NOT EXISTS happ_theme_tokens (
    token          VARCHAR(64) PRIMARY KEY,
    telegram_id    BIGINT      NOT NULL,
    remnawave_uuid VARCHAR(64) NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_accessed  TIMESTAMPTZ,
    access_count   INTEGER     NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_happ_theme_tokens_tg
    ON happ_theme_tokens(telegram_id);
