-- Migration 066: cashback_fixed_percent — admin-managed override.
--
-- Позволяет админу зафиксировать конкретный % кешбэка для конкретного
-- пользователя. Семантика:
--   NULL             — фикс выключен, работает обычная логика
--                      (тир на основе оплативших рефералов + grandfather-floor)
--   0..100           — жёсткое замещение: пользователь получает ИМЕННО
--                      этот %, невзирая на тир и cashback_floor_percent.
--                      Не суммируется.
--
-- Отличие от cashback_floor_percent:
--   floor  — нижняя граница (min). Тир может её превысить.
--   fixed  — точный %. Ни тир, ни floor не действуют, пока fixed=NOT NULL.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS cashback_fixed_percent INTEGER;

-- Sanity-check в SQL — валидация делается в коде, но не даём вставить
-- очевидный мусор (отрицательный / >100).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.constraint_column_usage
        WHERE table_name = 'users'
          AND constraint_name = 'chk_cashback_fixed_percent_range'
    ) THEN
        BEGIN
            ALTER TABLE users
                ADD CONSTRAINT chk_cashback_fixed_percent_range
                CHECK (cashback_fixed_percent IS NULL
                       OR (cashback_fixed_percent >= 0 AND cashback_fixed_percent <= 100));
        EXCEPTION WHEN duplicate_object THEN
            NULL;
        END;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_users_cashback_fixed
    ON users (cashback_fixed_percent)
    WHERE cashback_fixed_percent IS NOT NULL;
