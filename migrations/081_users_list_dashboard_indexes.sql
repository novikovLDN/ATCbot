-- Migration 081: индексы под GET /users/list и GET /audit/recent?telegram_id=
--
-- До этого экран «Пользователи» умел только точечный поиск, поэтому по
-- users никто не сортировал и не пагинировал. Новый листинг делает
-- ORDER BY <sort> LIMIT/OFFSET по всей таблице — без индекса это seq scan
-- + внешняя сортировка на каждой странице.
--
-- Отдельного индекса под фильтр `has_sub` нет намеренно: он выражается
-- через уже существующий idx_subscriptions_active_expiry (migration 024).

-- Дефолтная сортировка листинга.
CREATE INDEX IF NOT EXISTS idx_users_created_at
    ON users (created_at DESC);

-- sort=last_seen_at. Partial: у большинства старых юзеров колонка NULL
-- (появилась в migration 052), а NULL'ы всё равно уходят в NULLS LAST.
CREATE INDEX IF NOT EXISTS idx_users_last_seen_at
    ON users (last_seen_at DESC)
    WHERE last_seen_at IS NOT NULL;

-- sort=balance — «у кого лежат деньги» в топе.
CREATE INDEX IF NOT EXISTS idx_users_balance
    ON users (balance DESC)
    WHERE balance IS NOT NULL;

-- Фильтр source=payment|admin|trial.
CREATE INDEX IF NOT EXISTS idx_subscriptions_source
    ON subscriptions (source)
    WHERE source IS NOT NULL;

-- Фильтр expires_before («у кого истекает до») и sort=expires_at.
-- Не partial: в отличие от idx_subscriptions_active_expiry карточке нужны
-- и уже истёкшие / неактивные подписки.
CREATE INDEX IF NOT EXISTS idx_subscriptions_expires_at
    ON subscriptions (expires_at);

-- Аудит по конкретному юзеру. Два отдельных индекса, а не составной:
-- запрос — это OR по двум колонкам, планировщик соберёт BitmapOr.
CREATE INDEX IF NOT EXISTS idx_audit_log_target_user
    ON audit_log (target_user)
    WHERE target_user IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_audit_log_telegram_id
    ON audit_log (telegram_id);
