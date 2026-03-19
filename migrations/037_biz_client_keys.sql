-- Migration 037: Business client key management
-- Таблица гостевых VPN-ключей для бизнес-подписчиков
-- Один ключ = один визит клиента с ограниченным временем жизни

CREATE TABLE IF NOT EXISTS biz_client_keys (
    id SERIAL PRIMARY KEY,
    owner_telegram_id BIGINT NOT NULL,           -- владелец бизнес-подписки
    client_name TEXT NOT NULL DEFAULT '',          -- имя клиента/название ключа
    vless_url TEXT NOT NULL DEFAULT '',            -- сгенерированный VLESS ключ
    uuid TEXT NOT NULL DEFAULT '',                 -- UUID ключа в Xray
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,              -- когда истекает ключ
    revoked_at TIMESTAMPTZ,                       -- досрочный отзыв (NULL = активен)
    extended_count INT NOT NULL DEFAULT 0,         -- сколько раз продлевали
    notified_30min BOOLEAN NOT NULL DEFAULT FALSE  -- отправлено ли уведомление за 30 мин
);

CREATE INDEX IF NOT EXISTS idx_biz_keys_owner ON biz_client_keys(owner_telegram_id);
CREATE INDEX IF NOT EXISTS idx_biz_keys_expires ON biz_client_keys(expires_at) WHERE revoked_at IS NULL;

-- Настройки бизнес-лимитов (админ может менять через дашборд)
CREATE TABLE IF NOT EXISTS biz_settings (
    telegram_id BIGINT PRIMARY KEY,
    max_clients_per_day INT NOT NULL DEFAULT 25     -- макс ключей в день
);
