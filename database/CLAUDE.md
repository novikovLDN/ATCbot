# database — CLAUDE.md

Слой БД: **сырой asyncpg, БЕЗ ORM.** Дополняет корневой `CLAUDE.md`.

## UTC-контракт (частый источник багов) — ЖЕЛЕЗНО

Колонки `TIMESTAMP WITHOUT TIME ZONE`, asyncpg ожидает **naive** datetime.
- Всё, что пишется в БД → `_to_db_utc(dt)` (`database/core.py:41`) — кидает `ValueError`, если datetime
  не aware-UTC.
- Всё, что читается из БД → `_from_db_utc(dt)` (`database/core.py:53`).
- Сравнение времени в воркерах/клинапе — только по UTC. Наивный `datetime.now()` без tz — баг.

## Структура

`core.py` (пул, `_to/_from_db_utc`, `retry_async`-обёртки) + доменные модули: `users.py`,
`subscriptions.py`, `traffic.py`, `farm.py`, `admin.py`, `platega_subscriptions.py`,
`bypass_gift_links.py`, `marketing_links.py`, `scheduled_broadcasts.py`, `beta_applications.py`,
`reconciliation.py`. PostgreSQL (CI: `postgres:16-alpine`).

Флаг `database.DB_READY: bool` — глобальный guard деградированного режима (БД не проинициализирована →
бот отвечает, но БД-операции гейтятся). Хендлеры проверяют через `common/guards.py::ensure_db_ready_*`.

## Миграции — кастомный ранер (`migrations.py` в корне, НЕ alembic)

- Таблица `schema_migrations(version TEXT PRIMARY KEY, applied_at)`; файлы `migrations/NNN_*.sql` по номеру.
  Сейчас ~79, последняя `079_sub_pairs.sql`.
- **Правило (комментарий в `migrations.py`):** все миграции backward-compatible → откатываемы; код НЕ должен
  полагаться на немедленное наличие новых полей (миграции применяются отдельно); rollback-допущения
  документируются в комментарии миграции.
- **CI-гейт Migration Integrity:** прогоняет ВСЕ `migrations/*.sql` по порядку на чистой БД. **Новая
  миграция обязана применяться на пустой БД без ошибок**, иначе CI красный.

## Финансовые мутации

Одна транзакция, один коннекшн. **Никогда не держать открытое соединение/транзакцию во время
HTTP-вызова** (провайдер/VPN API). Инвариант: no double payment / no subscription loss / no UUID loss.
Списки «кто пишет в какую таблицу» — в `docs/data_ownership.md` (реальный, проектно-специфичный).
