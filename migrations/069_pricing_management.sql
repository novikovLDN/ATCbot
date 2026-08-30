-- Migration 069: pricing management — override цен тарифов + global-discount.
--
-- Позволяет админу через дашборд:
--   1) переопределить цену конкретного (tariff, period_days) без релиза;
--   2) включить глобальную скидку N% для всех юзеров с причиной и
--      сроком действия (например, «Летняя акция, до 25 июля»).
--
-- Bot-код читает цены через helper get_effective_price() — если для
-- пары есть override, берётся он; иначе — базовая из config.TARIFFS.
-- Затем к цене применяется global_discount_percent (если он активен
-- и NOW() < discount_until_at). Результат округляется до рубля.

CREATE TABLE IF NOT EXISTS tariff_price_overrides (
    tariff TEXT NOT NULL,
    period_days INTEGER NOT NULL,
    price_rub INTEGER NOT NULL CHECK (price_rub > 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by BIGINT,
    PRIMARY KEY (tariff, period_days)
);

-- Singleton — id=1 всегда. Хранит текущую глобальную скидку.
CREATE TABLE IF NOT EXISTS pricing_settings (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    -- 0 = скидки нет; 1..99 = скидка N% на всё.
    global_discount_percent INTEGER NOT NULL DEFAULT 0
        CHECK (global_discount_percent >= 0 AND global_discount_percent < 100),
    -- Причина/подпись для отображения юзеру («Летняя акция», «Чёрная пятница»).
    discount_reason TEXT,
    -- Дата истечения скидки (UTC). NULL = бессрочная (пока админ не выключит).
    discount_until_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by BIGINT
);

-- Гарантия одной строки-настроек.
INSERT INTO pricing_settings (id, global_discount_percent)
VALUES (1, 0)
ON CONFLICT (id) DO NOTHING;
