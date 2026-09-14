-- Migration 067: scheduled_broadcasts — отложенные и повторяющиеся рассылки
--
-- Позволяет админу запланировать рассылку на конкретную дату+время
-- (max +7 дней вперёд, Europe/Moscow), с опциональным повторением
-- (once / daily / weekdays / weekly).
--
-- Каждый запуск создаёт НОВЫЙ broadcasts row (полная история сохранена),
-- отдельно логируется в scheduled_broadcasts.run_count.
--
-- Хранение параметров подписи (title/message/photo/buttons/discount) —
-- копией на момент создания задания, чтобы при удалении/правке
-- исходной рассылки задание продолжало работать с зафиксированным
-- контентом.

CREATE TABLE IF NOT EXISTS scheduled_broadcasts (
    id SERIAL PRIMARY KEY,

    -- Ссылка на исходную рассылку (клонирование как источник). NULL если
    -- задание создано с нуля или если исходную удалили.
    source_broadcast_id INTEGER REFERENCES broadcasts(id) ON DELETE SET NULL,

    -- Снапшот параметров рассылки (независим от source_broadcast_id).
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    photo_file_id TEXT,
    buttons TEXT[],
    segment TEXT NOT NULL,

    -- Скидка/gift_reveal — те же поля что в broadcast_discounts.
    discount_percent INTEGER,
    discount_hours INTEGER,
    discount_label TEXT,
    gift_reveal_percent INTEGER,

    -- Расписание:
    -- scheduled_at — момент СЛЕДУЮЩЕГО запуска (UTC внутри БД,
    -- вычисляется с учётом Europe/Moscow при вводе админом).
    scheduled_at TIMESTAMPTZ NOT NULL,

    -- recurrence ∈ ('once', 'daily', 'weekdays', 'weekly')
    --   once     — разовая, отработает один раз и станет is_active=FALSE
    --   daily    — каждый день в то же время
    --   weekdays — только понедельник-пятница
    --   weekly   — раз в неделю в тот же день недели
    recurrence TEXT NOT NULL DEFAULT 'once'
        CHECK (recurrence IN ('once', 'daily', 'weekdays', 'weekly')),

    -- recurrence_end_at — когда прекратить повторения (NULL = навсегда,
    -- пока админ не отменит вручную). Игнорируется для recurrence='once'.
    recurrence_end_at TIMESTAMPTZ,

    -- Флаги + история запусков.
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_run_at TIMESTAMPTZ,
    last_run_broadcast_id INTEGER REFERENCES broadcasts(id) ON DELETE SET NULL,
    run_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,

    -- Meta.
    created_by BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cancelled_at TIMESTAMPTZ,
    cancelled_by BIGINT
);

-- Быстрая выборка «что пора запускать» — scheduler-worker дёргает
-- каждую минуту.
CREATE INDEX IF NOT EXISTS idx_scheduled_broadcasts_due
    ON scheduled_broadcasts (scheduled_at)
    WHERE is_active = TRUE;

-- Быстрый список активных для UI.
CREATE INDEX IF NOT EXISTS idx_scheduled_broadcasts_active
    ON scheduled_broadcasts (is_active, scheduled_at DESC);
