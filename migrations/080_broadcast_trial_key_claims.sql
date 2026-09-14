-- Идемпотентный ledger для broadcast-кнопки «🎁 Получить пробный ключ».
--
-- Подарок = +1 день подписки и +1 ГБ трафика обхода. Ограничение по решению
-- владельца: ОДИН раз на рассылку — юзер может забрать подарок один раз в
-- рамках каждой конкретной рассылки (broadcast_id). Повторный клик по той же
-- рассылке → toast «Подарок уже получен», без повторной выдачи.
--
-- PRIMARY KEY (broadcast_id, telegram_id) даёт атомарную идемпотентность через
-- INSERT ... ON CONFLICT DO NOTHING RETURNING. Таблица additive и backward-
-- compatible: старый код её не читает, новый — создаёт лениво.

CREATE TABLE IF NOT EXISTS broadcast_trial_key_claims (
    broadcast_id BIGINT NOT NULL,
    telegram_id  BIGINT NOT NULL,
    claimed_at   TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    PRIMARY KEY (broadcast_id, telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_broadcast_trial_key_claims_tg
    ON broadcast_trial_key_claims (telegram_id);
