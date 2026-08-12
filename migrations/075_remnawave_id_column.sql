-- 075: Remnawave 3.x migration — числовой user_id вместо uuid.
--
-- Remnawave panel 3.x убрал колонку `uuid` из объекта юзера. Идентификатором
-- на панели теперь служит `id` (BigInt). Старые `remnawave_uuid` /
-- `remnawave_premium_uuid` больше не годятся для вызовов `/api/users/{id}` —
-- надо кэшировать числовой id.
--
-- Оригинальные UUID колонки НЕ удаляются — они полезны как cross-reference
-- на исторические логи и как fallback для by-short-uuid лукапов.
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS remnawave_id BIGINT;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS remnawave_premium_id BIGINT;
CREATE INDEX IF NOT EXISTS idx_subscriptions_remnawave_id
    ON subscriptions(remnawave_id)
    WHERE remnawave_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_subscriptions_remnawave_premium_id
    ON subscriptions(remnawave_premium_id)
    WHERE remnawave_premium_id IS NOT NULL;
