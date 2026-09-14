-- Remnawave Panel 3.x переходит на числовой user.id (UUID удалён из ответов
-- полностью). Пре-миграция: кешируем numeric id для каждой активной подписки,
-- пока панель ещё 2.7.4 и отдаёт оба (uuid + id).
--
-- После апгрейда панели remnawave_uuid / remnawave_premium_uuid становятся
-- бесполезными (панель не принимает UUID в path и не отдаёт в response) —
-- оставляем колонки для истории, но использует код только *_id.

ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS remnawave_id INTEGER,
    ADD COLUMN IF NOT EXISTS remnawave_premium_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_subscriptions_remnawave_id
    ON subscriptions (remnawave_id)
    WHERE remnawave_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_subscriptions_remnawave_premium_id
    ON subscriptions (remnawave_premium_id)
    WHERE remnawave_premium_id IS NOT NULL;
