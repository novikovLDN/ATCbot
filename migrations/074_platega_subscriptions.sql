-- Migration 074: platega_subscriptions + platega_subscription_charges
--
-- Рекуррентные СБП-подписки через Platega (paymentMethod=6).
-- POST /transaction/process возвращает transactionId — это ID подписки
-- (subscription_id), и redirect-URL для окна привязки счёта (30 мин).
-- После привязки Platega сама шлёт callback'и на каждое списание с
-- ЗАГЛАВНЫМИ ключами (Id, Amount, Status, SubscriptionId, NextChargeAt).
--
-- Идемпотентность: PK на charge_id в platega_subscription_charges
-- (INSERT ... ON CONFLICT DO NOTHING). Дубли callback'ов не
-- продлевают подписку дважды.
--
-- MVP: только интервал=3 (месяц), только admin-visible кнопка
-- (см. platega_service.is_subscription_visible_to).
--
-- Migration 073 занята: реверт-конфликт с 073_happ_theme_tokens.sql,
-- поэтому нумерация прыгает через 072-073 до 074.

CREATE TABLE IF NOT EXISTS platega_subscriptions (
    subscription_id       VARCHAR(64) PRIMARY KEY,
    telegram_id           BIGINT       NOT NULL,
    amount_kopecks        INTEGER      NOT NULL,
    interval_days         INTEGER      NOT NULL,       -- 30/90/180/365
    tariff_type           VARCHAR(32)  NOT NULL,       -- basic/plus/etc
    status                VARCHAR(32)  NOT NULL DEFAULT 'PendingAgreement',
    customer_email        VARCHAR(255),
    description           TEXT,
    next_charge_at        TIMESTAMPTZ,
    last_charge_at        TIMESTAMPTZ,
    charges_success       INT          DEFAULT 0,
    charges_failed        INT          DEFAULT 0,
    total_amount_kopecks  BIGINT       DEFAULT 0,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_platega_sub_tg     ON platega_subscriptions(telegram_id);
CREATE INDEX IF NOT EXISTS idx_platega_sub_status ON platega_subscriptions(status);

-- Отдельная таблица для транзакций-списаний (идемпотентность по Callback.Id).
CREATE TABLE IF NOT EXISTS platega_subscription_charges (
    charge_id        VARCHAR(64) PRIMARY KEY,       -- Callback.Id (транзакция этого списания)
    subscription_id  VARCHAR(64) NOT NULL REFERENCES platega_subscriptions(subscription_id),
    telegram_id      BIGINT      NOT NULL,
    amount_kopecks   INTEGER     NOT NULL,
    status           VARCHAR(32) NOT NULL,           -- CONFIRMED / CANCELED
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_platega_charge_sub ON platega_subscription_charges(subscription_id);
