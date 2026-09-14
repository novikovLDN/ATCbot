-- Migration 068: automated_notifications — управление зашитыми
-- в код автоуведомлениями бота (reminder-триал-3ч, expiry-24h,
-- welcome и др.) без релиза.
--
-- Модель: код регистрирует свои notification-keys через registry.py
-- при старте (upsert в automated_notifications с текстом-дефолтом).
-- Админ через дашборд может:
--   - переопределить template_text (NULL = использовать defaults из кода)
--   - is_enabled = FALSE → полностью выключить (bot-код проверяет)
--   - trigger_config (JSONB) — для reminders: сдвинуть окно отправки
--
-- Отправка логируется в automated_notification_sends — для admin-stats.

CREATE TABLE IF NOT EXISTS automated_notifications (
    -- Ключ вида 'trial.reminder_24h' — совпадает с i18n-ключом там,
    -- где это возможно, чтобы минимизировать когнитивную нагрузку.
    key TEXT PRIMARY KEY,

    -- Админ-friendly название на русском («Триал: за 24ч до истечения»).
    title TEXT NOT NULL,

    -- Пояснение: когда шлётся, кому, почему нужно.
    description TEXT,

    -- Категория для группировки в UI: 'trial', 'subscription', 'welcome',
    -- 'payment', 'referral', 'gift', 'other'.
    category TEXT NOT NULL DEFAULT 'other',

    -- Заводское значение — обновляется на каждом старте бота из registry.
    -- Служит fallback'ом и точкой reset'а.
    default_text_ru TEXT NOT NULL,

    -- Кастомный override админа. NULL → используется default_text_ru.
    custom_text_ru TEXT,

    -- Если FALSE — bot-код должен пропустить отправку.
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,

    -- JSONB для настроек timing/segment/etc. Формат зависит от типа:
    --   reminder: {"before_expiry_hours": 24}
    --   scheduled: {"cron": "0 10 * * *"}
    --   Свободный dict — код notification-sender'а сам знает, что читать.
    trigger_config JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Список плейсхолдеров, которые ждёт template (для help в UI).
    -- ["username", "days_left"] и т.п.
    template_vars TEXT[] NOT NULL DEFAULT '{}',

    -- Аудит.
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_edited_by BIGINT
);

CREATE INDEX IF NOT EXISTS idx_automated_notifications_category
    ON automated_notifications (category);

-- Лог отправок для админ-stats. Одна строка на send-попытку.
CREATE TABLE IF NOT EXISTS automated_notification_sends (
    id BIGSERIAL PRIMARY KEY,
    key TEXT NOT NULL,
    telegram_id BIGINT NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- 'sent' | 'failed' | 'skipped_disabled' | 'blocked'
    status TEXT NOT NULL DEFAULT 'sent',
    -- Опциональная ошибка от Telegram.
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_ans_key_sent
    ON automated_notification_sends (key, sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_ans_telegram_id
    ON automated_notification_sends (telegram_id, sent_at DESC);
