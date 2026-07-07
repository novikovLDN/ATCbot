-- Migration 065: Stats-links + Promo-links.
--
-- Две новые сущности для маркетинга-админки:
--
--   stats_links   — «отслеживающие» ссылки. Админ вводит имя, получает
--                    красивую короткую ссылку `?start=s-<slug>`.
--                    По клику записываем визит; при активации триала /
--                    первой покупке — добавляем в атрибуцию. Даёт
--                    воронку «клик → триал → покупка» на конкретную ссылку.
--
--   promo_links   — ссылка с наградой. Админ выбирает тип награды
--                    (subscription_days / tariff_discount / bypass_discount /
--                    bypass_gb) и значение. Пользователь по клику
--                    получает награду. Лимиты: max_uses_per_user (по
--                    умолчанию 1), max_uses_total (nullable = ∞).
--
-- Атрибуция для stats_links — колонка users.acquired_via_stat_link_id.
-- Пишется на ПЕРВОМ клике по stat-ссылке; последующие клики только
-- инкрементят clicks-счётчик, но не переписывают источник (иначе
-- сорвётся LTV аналитика).

CREATE TABLE IF NOT EXISTS stats_links (
    id SERIAL PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    deactivated_at TIMESTAMPTZ,
    reactivated_at TIMESTAMPTZ,
    created_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stats_links_slug ON stats_links (slug);
CREATE INDEX IF NOT EXISTS idx_stats_links_active ON stats_links (is_active);

-- Клики. Одна строка = один визит по ссылке (может быть повторный от
-- того же юзера — считаем только уникальных для «trial-конверсии»,
-- но общее число кликов = COUNT(*)).
CREATE TABLE IF NOT EXISTS stats_link_clicks (
    id SERIAL PRIMARY KEY,
    link_id INTEGER NOT NULL REFERENCES stats_links(id) ON DELETE CASCADE,
    telegram_id BIGINT NOT NULL,
    is_first_click BOOLEAN NOT NULL DEFAULT FALSE,
    is_new_user BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stats_link_clicks_link ON stats_link_clicks (link_id);
CREATE INDEX IF NOT EXISTS idx_stats_link_clicks_tg ON stats_link_clicks (telegram_id);

-- Атрибуция пользователя. Если stat-ссылка первая, откуда пришёл, —
-- заполняется. Раз заполнено — не переписывается.
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS acquired_via_stat_link_id INTEGER
        REFERENCES stats_links(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_users_acquired_via_stat
    ON users (acquired_via_stat_link_id)
    WHERE acquired_via_stat_link_id IS NOT NULL;


-- ── Promo links ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS promo_links (
    id SERIAL PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    -- reward_type ∈ (
    --   'subscription_days',  reward_value = число дней (3/7/14/30/90/180/365)
    --   'tariff_discount',    reward_value = процент (10/15/20/25/30/35/40/45/50)
    --   'bypass_discount',    reward_value = процент (тот же ряд)
    --   'bypass_gb'           reward_value = число ГБ (5/10/15/20/...)
    -- )
    reward_type TEXT NOT NULL,
    reward_value INTEGER NOT NULL,
    -- Опциональные параметры конкретного типа награды.
    -- Для subscription_days: tariff='basic'/'plus' (по умолчанию 'basic').
    -- Для tariff_discount: сколько часов действует (по умолчанию 24).
    -- Хранятся в JSONB для гибкости.
    reward_meta JSONB DEFAULT '{}',
    max_uses_total INTEGER,          -- NULL = unlimited
    max_uses_per_user INTEGER NOT NULL DEFAULT 1,
    used_count INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    deactivated_at TIMESTAMPTZ,
    reactivated_at TIMESTAMPTZ,
    created_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ            -- опциональная дата истечения
);

CREATE INDEX IF NOT EXISTS idx_promo_links_slug ON promo_links (slug);
CREATE INDEX IF NOT EXISTS idx_promo_links_active ON promo_links (is_active);

-- Редемпции — кто и когда использовал промо-ссылку.
-- UNIQUE (link_id, telegram_id) enforce'ит max_uses_per_user=1 на DB-уровне
-- для типового случая; если max_uses_per_user>1 (редко) — enforce'им в коде.
CREATE TABLE IF NOT EXISTS promo_link_redemptions (
    id SERIAL PRIMARY KEY,
    link_id INTEGER NOT NULL REFERENCES promo_links(id) ON DELETE CASCADE,
    telegram_id BIGINT NOT NULL,
    reward_type_snapshot TEXT NOT NULL,
    reward_value_snapshot INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_promo_link_redemptions_link
    ON promo_link_redemptions (link_id);
CREATE INDEX IF NOT EXISTS idx_promo_link_redemptions_tg
    ON promo_link_redemptions (telegram_id);
