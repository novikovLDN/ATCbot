# Remnawave 2.7.4 → 3.x — план миграции

**Целевая версия**: 3.2.3 (последняя стабильная на 2026-08-16).
**Текущая**: 2.7.4.

## 0. Оперативные артефакты уже готовы

- **Backup-ветка**: `backup/remnawave-2.7.4-pre-migration` (запушена на origin). Полный слепок кода с текущим 2.7.4-клиентом на случай быстрого отката.
- Полный audit кода — в разделе 3 (обновится, когда Explore-агент закончит).

Rollback = `git checkout backup/remnawave-2.7.4-pre-migration && deploy`. Обратно к 2.7.4 клиенту откатимся за минуту.

---

## 1. Breaking changes 2.7.4 → 3.0.0 — что реально ломается

### 1.1 Идентификатор юзера: `uuid` → `id` (integer)

**Самое главное изменение.** В 3.0 из модели User **выпилен UUID**, юзер теперь идентифицируется числовым `id` (integer, автоинкремент).

Последствия для нас — везде, где мы храним/передаём `remnawave_uuid`, `remnawave_premium_uuid` как UUID-строки, нужно либо:
- **Option A (проще)**: продолжать хранить `uuid` в панели (панель отдаёт его в поле `uuid` в response — но path-параметр теперь `id`). После миграции: получить у панели новый `id` для каждого нашего юзера, сохранить в DB рядом с UUID.
- **Option B**: переехать на численный `id` полностью. Требует миграции ~2500 наших pending row'ов + аудита всех вызовов.

Наш audit агентом покажет, где конкретно UUID используется как ключ доступа. По грубой оценке — **все 11 функций в `app/services/remnawave_api.py`** плюс кеш в `remnawave_uuid` / `remnawave_premium_uuid` колонках БД (migration 048 добавила эти колонки).

### 1.2 Endpoint URL изменились

Изменения в путях (все с префиксом `/api/`):

| Действие | 2.7.4 | 3.x |
|---|---|---|
| По username | `/users/by-username/{username}` | `/users/username/{username}` |
| По shortUuid | `/users/by-short-uuid/{shortUuid}` | `/users/short-uuid/{shortUuid}` |
| Создать | `POST /users` | `POST /users/create` |
| Обновить | `PATCH /users` (или наш auto-discover) | `PATCH /users/update` |
| Получить (list) | `GET /users` | `GET /users/get` |
| Получить (по id) | `GET /users/{uuid}` | `GET /users/{userId}` |
| Удалить | `DELETE /users/{uuid}` | `DELETE /users/delete/{userId}` |
| Enable | `PATCH /users` с `status=ACTIVE` | `POST /users/{userId}/actions/enable` |
| Disable | `PATCH /users` с `status=DISABLED` | `POST /users/{userId}/actions/disable` |
| Reset traffic | `POST /users/{uuid}/reset-traffic` | `POST /users/{userId}/actions/reset-traffic` |
| Revoke subscription | (не было) | `POST /users/{userId}/actions/revoke` |
| Extend expiry | `PATCH /users` c `expireAt=…` | `POST /users/{userId}/actions/extend` (dedicated) |

**HWID (изменился HTTP verb):**
| Действие | 2.7.4 | 3.x |
|---|---|---|
| Список устройств юзера | `GET /hwid/devices/{userUuid}` | `GET /hwid/devices/{userId}` |
| Удалить одно | `POST /hwid/devices/delete` | `DELETE /hwid/devices/delete` |
| Удалить все | `POST /hwid/devices/delete-all` | `DELETE /hwid/devices/delete-all` |

Bulk-операции реорганизованы в `/users/bulk/*` (нам не критично — не используем).

### 1.3 Response headers для HWID

`x-hwid-limit` → **`x-hwid-active`** (кастомный header в ответе `subscription` для клиентов). Мы этот header не парсим — не критично.

### 1.4 Panel-side (для тебя, не для нашего кода)

- **v2.8.0**: single `tag` → multiple `tags` для hosts. DB миграция автоматически (Prisma).
- **v2.8.1**: env var `JWT_AUTH_SECRET` → `APP_SECRET`. **Переименовать перед деплоем 2.8.1** иначе панель не поднимется.
- **v3.0.0**: UUID drop, `id` restructuring — Prisma миграция автоматически.
- **v3.2.x**: включает переезд Node.js на 24.19.
- Обязательный **бэкап БД панели + `.env`** перед апгрейдом самой панели.

### 1.5 Стандартизация ошибок

Все ошибки (400/404/500) типизированы и задокументированы в OpenAPI. Наш код обрабатывает `A063` (User with specified params not found) и `A019` (traffic add related) — надо проверить, изменились ли коды в 3.0. По changelog **error codes НЕ переименованы**, только структура response стандартизирована.

---

## 2. Стратегия миграции

Три варианта, ранжированы по риску:

### Вариант A: Заменить наш ручной httpx-клиент на официальный SDK `remnawave-api` (PyPI)

**За:**
- Гарантия правильных endpoint'ов, DTO, error handling.
- Автоматически подтягиваем новые версии панели (3.2.x, 3.3.x) с новыми фичами.
- Escape hatch если Remnawave снова сломает API — обновим SDK одной строчкой в `requirements.txt`.

**Против:**
- Async-only (наш код и так async — OK).
- Зависимости: `orjson`, `rapid-api-client==0.6.0`, `httpx<0.28.0`. Проверить конфликт с нашими `httpx>=0.27.2`. **Наш `httpx` уже <0.28 — OK.**
- Нужно переписать `_is_our_entity` / preflight / A019 recovery поверх SDK.

**Установка**:
```bash
pip install remnawave-api  # v3.2.2, требует Python 3.11-3.14
```

### Вариант B: Переписать `app/services/remnawave_api.py` под новые endpoints вручную

**За:**
- Полный контроль, минимум внешних зависимостей.
- Знаем каждый вызов.

**Против:**
- Заново все boilerplate + retry + auto-discover (я его в 3.0 бы убрал — endpoint'ы теперь стабильные).
- Легко пропустить edge case.

### Вариант C: Оставить свой клиент, но добавить version-shim

Если панель какое-то время сможет отвечать И на старые, И на новые URL'ы (что маловероятно — 3.0 не backwards-compatible), то shim: определяем версию через `GET /api/system/config`, роутим по флагу.

**Рекомендация**: **Вариант A** (`remnawave-api` SDK). Меньше кода, официальная поддержка. `remnactual` — форк для 3.0 если что-то с mainstream SDK.

---

## 3. Наш код — детальный audit (заполняется агентом)

_Секция обновится когда Explore-агент закончит анализ `app/services/remnawave_*`. Пока — известные функции по grep:_

- `app/services/remnawave_api.py` — 11 async функций (базовый httpx клиент)
- `app/services/remnawave_bypass.py` — bypass-tier operations (`create_bypass_user_entity`, `add_bypass_traffic`, `delete_bypass_user`, `_is_our_entity`, `_backfill_telegram_id`)
- `app/services/remnawave_premium.py` — premium-tier operations
- `app/services/remnawave_service.py` — legacy service (используется reconciler'ами)

Cache columns в БД (migration 048):
- `remnawave_uuid` (bypass tier)
- `remnawave_premium_uuid` (premium tier)
- `remnawave_subscription_url`
- `remnawave_short_uuid`

Все ниже — под UUID. При переходе на 3.0 либо (a) продолжаем хранить UUID (панель отдаёт в `response.uuid`, использовать в качестве fallback identifier), либо (b) добавляем колонки `remnawave_id` / `remnawave_premium_id` (integer).

**Рекомендуется (a)** — минимум миграций БД. Панель после 3.0 ещё будет отдавать `uuid` в response'ах для совместимости с существующими клиентами (не как path-param).

---

## 4. Грабли, известные заранее

1. **Не апгрейдить сразу с 2.7.4 → 3.0.** Правильная последовательность:
   - Backup БД + `.env`.
   - **2.7.4 → 2.8.0**: миграция `tags` для hosts (автоматически, но обязательно перед 2.8.1).
   - **2.8.0 → 2.8.1**: переименовать `JWT_AUTH_SECRET` → `APP_SECRET` в `.env` **до** запуска 2.8.1.
   - **2.8.1 → 3.0.0**: UUID drop, id restructure (Prisma migration).
   - **3.0.0 → 3.2.3**: минорные обновления, без breaking.

2. **HTTP verb для HWID delete**: `POST` → `DELETE`. Легко пропустить, если тестировать только через bot flow.

3. **`by-username/` → `username/`**: наш `find_user_by_username` в `remnawave_api.py:444` использует старый path. Это **основной путь recovery** при adopting existing entity (см. `_is_our_entity` + backfill `telegramId`). Если пропустим — сломается bypass A019 recovery, юзеры получат `conflict_unrelated_user`.

4. **`_update_method` auto-discover больше не нужен**: в 3.0 endpoint'ы стабильные. Наш код пробует `PUT /api/users/{uuid}`, `POST /api/users/{uuid}`, `PATCH /api/users`, `POST /api/users/update`, `PUT /api/users` — в 3.0 надо просто `PATCH /api/users/update`. **Удалить `_update_method` cache** после миграции, иначе первый вызов может залипнуть на 404.

5. **Existing pending purchases в момент переключения**: если ретрайт webhook'а придёт со старым UUID в БД — reconciler после переключения на 3.0 попробует GET по UUID → 404 → пометит expired (в старом коде) или crash (в новом, если не защищено). **Feature flag `REMNAWAVE_API_VERSION` в config** — переключить в момент деплоя, все pending до этого момента ретрайнутся под новым клиентом.

6. **Кэш `remnawave_uuid` в БД остаётся** — панель отдаёт `response.uuid` в 3.0 (для совместимости), просто не принимает как path-param. Наш кэш валиден. Но **добавить fallback lookup**: если по uuid → 404, то `by-username` → получить `id` → PATCH через `/users/update` body с `uuid` или новым `id`. Прописать в `_is_our_entity` username-based recovery (уже реализовано в fix 62).

7. **Response `paymentDetails` в webhook** — не Remnawave, но напоминание: любые провайдеры могут возвращать по-разному. Логировать raw responses в staging.

8. **HWID `disableHwidCheck`** (v2.8.0+): в response rules можно отключить проверку HWID. Проверить, что наш `traffic_monitor` не полагается на HWID lookup (он тянет `usedTrafficBytes` из user endpoint, не из HWID — OK).

9. **Stream endpoint `/users/stream`** — новый, для больших массовых операций. Нам сейчас не нужен, но при массовой синхронизации (например импорт всех юзеров) можно использовать вместо `GET /users` с пагинацией.

10. **Redis Streams export** (v3.0+): панель может пушить события юзеров/subscription requests в Redis Streams. Мы этим не пользуемся, но при необходимости — более надёжный canal чем polling `traffic_monitor`. Можно опционально включить позже.

---

## 5. Чек-лист миграции (пошагово)

### Фаза 1 — Подготовка (без даунтайма)

- [x] Создать `backup/remnawave-2.7.4-pre-migration` бранч (сделано).
- [ ] Дождаться audit-агента для полного mapping'а вызовов.
- [ ] Написать `docs/REMNAWAVE_3_MIGRATION_MAPPING.md` — old → new endpoints per каждой функции.
- [ ] Snapshot БД панели (dump) + `.env` — сохранить где-то отдельно от panel-хоста.
- [ ] Snapshot нашей БД (`pending_purchases`, `subscriptions`, `remnawave_uuid` cache).
- [ ] Проверить, что все юзеры в панели имеют `telegramId` — иначе после миграции их не «adopt» через username fallback. Скрипт: `SELECT COUNT(*) FROM users WHERE telegram_id IS NULL`. Backfill вручную если нужно.

### Фаза 2 — Обновление панели (короткий downtime, ~10 мин)

- [ ] Апгрейд **panel 2.7.4 → 2.8.0** (Docker image change).
- [ ] Апгрейд **panel 2.8.0 → 2.8.1** — предварительно переименовать `JWT_AUTH_SECRET` → `APP_SECRET`.
- [ ] Апгрейд **panel 2.8.1 → 3.0.0** — сразу ставится Prisma миграция (UUID drop, id restructure).
- [ ] Апгрейд **3.0.0 → 3.2.3** (5 мажорных версий с фичами, минимум breaking).
- [ ] Проверить `/api/system/config`, `/health` — панель поднялась.

### Фаза 3 — Обновление бота (feature-flagged)

Реализуем на бранче `feat/remnawave-v3-migration`, деплоим за флагом `REMNAWAVE_API_VERSION=3`:

- [ ] Установить `remnawave-api` в `requirements.txt` (либо переписать `remnawave_api.py` вручную).
- [ ] Обновить `remnawave_api.py`:
  - `find_user_by_username`: `/by-username/` → `/username/`.
  - `find_user_by_short_uuid`: `/by-short-uuid/` → `/short-uuid/`.
  - `create_user`: `POST /` → `POST /create`.
  - `update_user`: убрать `_update_method` auto-discover, использовать `PATCH /update` body-based.
  - `get_user`: `/users/{uuid}` работает если наша БД помнит uuid (панель отдаёт uuid в response). Fallback на `/username/{username}` → id → используем id для actions endpoints.
  - `delete_user`: `DELETE /users/{uuid}` → `DELETE /users/delete/{userId}`. **Нужен id, не uuid.**
  - `reset_user_traffic`: `POST /reset-traffic` → `POST /{userId}/actions/reset-traffic`. **Id required.**
  - HWID: `POST /hwid/devices/delete` → `DELETE /hwid/devices/delete` (только verb).
- [ ] Обновить `remnawave_bypass.py` / `remnawave_premium.py`:
  - `_is_our_entity` — уже проверяет username fallback (fix 62), OK.
  - Adoption path: получить `id` через `/username/{username}` → сохранить в кеш → использовать для actions endpoints.
- [ ] Добавить колонки `remnawave_id`, `remnawave_premium_id` (integer) в `subscriptions` таблицу — миграция 076. Заполнять при adoption/create.
- [ ] Обновить `remnawave_service.py` legacy код (используется в `wata_reconciler`, `traffic_monitor`).
- [ ] Обновить `add_bypass_traffic` — сейчас PATCH через body-based update. В 3.0 останется PATCH через `/users/update`, но `uuid` в body заменить на `id` (нужен fallback).

### Фаза 4 — Тестирование в staging

- [ ] Полный E2E: создать нового юзера, купить подписку, продлить, купить трафик, удалить.
- [ ] E2E для existing юзеров: recovery через username, backfill `telegramId`, adopt entity.
- [ ] Проверить reconciler `wata_reconciler` — что pending purchases обрабатываются.
- [ ] Проверить `traffic_monitor` (каждые 5 мин) — что читает `usedTrafficBytes`.
- [ ] Проверить fast-expiry-cleanup — что удаляет юзеров.
- [ ] Load-test: 100 запросов подряд, RATE_LIMIT не бьёт.

### Фаза 5 — Продакшн-переключение

- [ ] Merge `feat/remnawave-v3-migration` в main.
- [ ] Деплой бота с `REMNAWAVE_API_VERSION=3` (флаг).
- [ ] Мониторинг: логи, error rate по Remnawave endpoints, `admin_alerts`.
- [ ] Если что-то сломалось: revert `REMNAWAVE_API_VERSION=2`, панель `docker-compose pull remnawave=2.7.4` (backup image), Prisma migration rollback через `pg_restore` из snapshot'а.

---

## 6. Что делать сейчас (до момента переключения)

1. **Не спешить** — панель Remnawave активно развивается, версия 3.2.3 совсем свежая (10 августа 2026). Подождать 1-2 недели, посмотреть на community — если issues всплывают, зафиксить перед миграцией.
2. **Заполнить `telegramId`** для всех existing entities в панели — чтобы после `_is_our_entity` через username fallback у нас не было конфликтов. Скрипт-миграция на panel-стороне.
3. **Убедиться, что все юзеры имеют username = str(telegram_id)** — это наш default `build_bypass_username`. Если у кого-то custom username — record'нуть, чтобы после миграции найти.
4. **Аудит `_update_method` cache** — понять, какой из вариантов discover'а сейчас закешен в проде. Логи `REMNAWAVE_UPDATE_DISCOVERED` покажут.

---

## 7. Rollback plan

Быстрый:
```bash
git checkout backup/remnawave-2.7.4-pre-migration
# rebuild container, redeploy
```

Полный (если апгрейд панели тоже нужно откатить):
```bash
# 1. Наш бот
git checkout backup/remnawave-2.7.4-pre-migration
docker-compose up -d --build atlas-bot

# 2. Панель — восстановить из backup image + БД
docker-compose pull remnawave:2.7.4
# Restore БД из snapshot'а перед миграцией
pg_restore -d remnawave_panel_db backup_before_v3.dump
# Restore .env (JWT_AUTH_SECRET вместо APP_SECRET)
cp .env.backup .env
docker-compose up -d remnawave
```

Держать snapshot БД панели + `.env` + docker image tag `2.7.4` доступными **минимум 2 недели** после переключения.

---

## Источники

- [Remnawave panel releases](https://github.com/remnawave/panel/releases)
- [Remnawave backend releases](https://github.com/remnawave/backend/releases) — детальные changelog по версиям
- [remnawave-api PyPI](https://pypi.org/project/remnawave-api/) — официальный Python SDK, v3.2.2
- [remnactual PyPI](https://pypi.org/project/remnactual/) — community-форк, специально под 3.0
- [Case211/remnawave-admin](https://github.com/Case211/remnawave-admin) — референсная интеграция для 3.0+
- [BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot](https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot) — telegram-bot интеграция для 3.0+
