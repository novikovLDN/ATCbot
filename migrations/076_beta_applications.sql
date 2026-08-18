-- Migration 076: beta_applications table
--
-- Хранит заявки на участие в бета-тестировании (VPN-Инноватор и т.п.).
-- Юзер жмёт кнопку «Оставить заявку» в рассылке → мы записываем сюда.
-- Идемпотентно: (telegram_id, program) UNIQUE — повторный клик не создаст
-- дубль.  Записываем username / first_name для админа, source_broadcast_id
-- для аналитики (какая рассылка сколько привела).

CREATE TABLE IF NOT EXISTS beta_applications (
    id                    BIGSERIAL PRIMARY KEY,
    telegram_id           BIGINT      NOT NULL,
    program               TEXT        NOT NULL DEFAULT 'vpn_innovator',
    username              TEXT,
    first_name            TEXT,
    applied_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_broadcast_id   BIGINT,
    UNIQUE (telegram_id, program)
);

CREATE INDEX IF NOT EXISTS idx_beta_applications_program_applied_at
    ON beta_applications(program, applied_at DESC);

CREATE INDEX IF NOT EXISTS idx_beta_applications_source_broadcast
    ON beta_applications(source_broadcast_id)
    WHERE source_broadcast_id IS NOT NULL;
