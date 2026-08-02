-- Migration 073: перенести в миграции последнее, что жило только в коде.
--
-- ЗАЧЕМ
-- Схема управлялась двумя механизмами сразу: файлами migrations/*.sql и
-- ~700 строками императивного DDL внутри database/core.py, которые
-- выполнялись при КАЖДОМ старте бота. Это 116 операторов CREATE TABLE /
-- ALTER TABLE / CREATE INDEX, и каждый просит у Postgres ACCESS EXCLUSIVE
-- на свою таблицу. Одна висящая idle-in-transaction сессия или работающий
-- autovacuum — и ALTER встаёт в очередь, а за ним встают все читающие
-- запросы к users, subscriptions, payments. Спасал только lock_timeout=5s,
-- после которого ошибка молча проглатывалась: на девственной базе колонка
-- просто не создавалась, и никто об этом не узнавал.
--
-- Сверка показала, что почти всё из core.py уже покрыто миграциями. Не
-- покрытыми оставались две таблицы и 26 колонок — они здесь. После этой
-- миграции источник истины по схеме один: каталог migrations/.
--
-- Все операторы идемпотентны (IF NOT EXISTS), поэтому миграция безопасна и
-- на боевой базе, где эти объекты давно созданы кодом: она не сделает
-- ничего, кроме записи в schema_migrations.

-- ── Таблицы, создававшиеся только в core.py ──────────────────────────

CREATE TABLE IF NOT EXISTS gift_subscriptions (
    id SERIAL PRIMARY KEY,
    gift_code TEXT UNIQUE NOT NULL,
    buyer_telegram_id BIGINT NOT NULL,
    tariff TEXT NOT NULL,
    period_days INTEGER NOT NULL,
    price_kopecks INTEGER NOT NULL,
    purchase_id TEXT,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'activated', 'expired')),
    activated_by BIGINT,
    activated_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_traffic_discounts (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    discount_percent INTEGER NOT NULL,
    expires_at TIMESTAMP NULL,
    created_by BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── Колонки, добавлявшиеся только в core.py ──────────────────────────

ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS correlation_id TEXT;
ALTER TABLE broadcast_log ADD COLUMN IF NOT EXISTS message_id BIGINT;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS notification_sent BOOLEAN DEFAULT FALSE;

ALTER TABLE pending_purchases ADD COLUMN IF NOT EXISTS country TEXT;
ALTER TABLE pending_purchases ADD COLUMN IF NOT EXISTS is_combo BOOLEAN DEFAULT FALSE;

ALTER TABLE referrals ADD COLUMN IF NOT EXISTS first_paid_at TIMESTAMP;

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS activation_attempts INTEGER DEFAULT 0;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS activation_status TEXT DEFAULT 'active';
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS country TEXT;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS is_bypass_only BOOLEAN DEFAULT FALSE;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS is_combo BOOLEAN DEFAULT FALSE;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS last_activation_error TEXT;

-- Флаги уведомлений по пробному периоду.
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_notif_6h_sent BOOLEAN DEFAULT FALSE;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_notif_18h_sent BOOLEAN DEFAULT FALSE;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_notif_30h_sent BOOLEAN DEFAULT FALSE;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_notif_42h_sent BOOLEAN DEFAULT FALSE;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_notif_54h_sent BOOLEAN DEFAULT FALSE;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_notif_60h_sent BOOLEAN DEFAULT FALSE;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_notif_71h_sent BOOLEAN DEFAULT FALSE;

ALTER TABLE users ADD COLUMN IF NOT EXISTS smart_offer_sent BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS special_offer_created_at TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS traffic_notified_5gb BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS traffic_notified_8gb BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_completed_sent BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_expires_at TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_used_at TIMESTAMP;
