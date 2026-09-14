# Remnawave 2.7.4 → 3.x — точный mapping API endpoints

Дополнение к `REMNAWAVE_3_MIGRATION.md`. Составлено после полного audit'а нашего кода (agent scan `app/services/remnawave_*`, все callers, workers, migrations).

## 0. Ключевые артефакты нашей интеграции

- Auth: `Authorization: Bearer <REMNAWAVE_API_TOKEN>` (env alias `REMNAWAVE_TOKEN` тоже поддерживается).
- Base: `config.REMNAWAVE_API_URL` (без trailing `/`).
- Timeout: `connect=5s / read=10s / write=5s / pool=5s`.
- Response envelope: `{"response": ...}` — распаковывается в `_request` / `_request_raw` (`remnawave_api.py:32,70`). **Если 3.0 уберёт envelope — сломает высокоуровневый код** (риск №9 в отчёте).

## 1. Users endpoints — точный old → new mapping

| Функция клиента | 2.7.4 (наш код сейчас) | 3.x (после) | Комментарий |
|---|---|---|---|
| `create_user` (`remnawave_api.py:110`) | `POST /api/users` body `{username, shortUuid, trafficLimitBytes, trafficLimitStrategy, status, expireAt, deviceLimit, [vlessUuid], [description], [telegramId], [externalSquadUuid], activeInternalSquads:[squadUuid]}` | `POST /api/users/create` body тот же | Path изменён, body **скорее всего** тот же. Проверить `vlessUuid` — v3.0 могло его снова смёржить с `uuid`. |
| `get_user(uuid)` (`:273`) | `GET /api/users/{uuid}` | `GET /api/users/{userId}` (где `userId` = integer) | **Основной риск №1.** Нужен либо `id` в кеше (сейчас храним только UUID), либо `by-username` → id lookup. Панель, вероятно, всё ещё принимает UUID в этой точке (для совместимости), но нужно проверить на staging. |
| `get_all_users(page_size, start)` (`:278`) | `GET /api/users?size=1000&start=N` | `GET /api/users/get?size=1000&start=N` | Path префикс `/get` добавлен. Пагинация та же (не курсорная). |
| `update_user(uuid, **fields)` (`:346`) | Auto-discover: `PUT /users/{uuid}`, `POST /users/{uuid}`, **`PATCH /users`**, `POST /users/update`, `PUT /users` — первый работающий кешируется в `_update_method` | `PATCH /api/users/update` body `{"uuid": uuid, **fields}` | **Риск №1 из audit.** `_update_method` cache надо СБРОСИТЬ на первом старте под 3.0, иначе может залипнуть на deprecated варианте. В 3.0 канонично — `PATCH /users/update` body-based. |
| `reset_user_traffic(uuid)` (`:381`) | `POST /api/users/{uuid}/reset-traffic` | `POST /api/users/{userId}/actions/reset-traffic` | **Path переехал под `/actions/`.** Нужен `userId` (integer) в path. |
| `delete_user(uuid)` (`:427`) | `DELETE /api/users/{uuid}` | `DELETE /api/users/delete/{userId}` | **Префикс `/delete/` + `userId`.** |
| `find_user_by_username(username)` (`:444`) | `GET /api/users/by-username/{name}` | `GET /api/users/username/{name}` | **Убрать `by-` префикс.** Критично для `_is_our_entity` recovery. |
| **(нет)** — новое в 3.0 | — | `POST /api/users/{userId}/actions/enable` | Раньше `update_user(status=ACTIVE)`. |
| **(нет)** — новое в 3.0 | — | `POST /api/users/{userId}/actions/disable` | Раньше `update_user(status=DISABLED)`. |
| **(нет)** — новое в 3.0 | — | `POST /api/users/{userId}/actions/revoke` | Раньше не было отдельного endpoint'а. |
| **(нет)** — новое в 3.0 | — | `POST /api/users/{userId}/actions/extend` | Раньше `update_user(expireAt=…)`. Можно продолжать через PATCH, но dedicated быстрее и надёжнее. |
| **(нет)** — новое в 3.0 | — | `GET /api/users/stream` | Для массового scan вместо пагинации `get_all_users`. Опционально для admin reconcile — быстрее чем 10 страниц по 1000. |
| **(нет)** — новое в 3.0 | — | `GET /api/users/short-uuid/{shortUuid}`, `GET /api/users/resolve`, `GET /api/users/tags/get` | Path префиксы `by-` убраны (`by-short-uuid` → `short-uuid`). |
| `assign_user_to_squad(uuid, squad)` (`:214`) | **4 fallback пути**: (1) `POST /api/squads/add-users-to-squad`, (2) `POST /api/squads/{squad}/users` × 2 body-варианта, (3) `PATCH/POST/PUT /api/users` с `activeInternalSquads` | Проверить `/api/squads/*` в v3.0 backend contract | **Риск №2 из audit.** Проверить актуальный контракт. `activeInternalSquads` в v3.0 может быть переименован в `internalSquadUuids` или `squads` — надо смотреть backend `libs/contract/models/users.model.ts`. |

## 2. HWID endpoints

| Функция | 2.7.4 | 3.x | Изменение |
|---|---|---|---|
| `get_user_hwid_devices(user_uuid)` (`:401`) | `GET /api/hwid/devices/{userUuid}` | `GET /api/hwid/devices/{userId}` | Path parameter `userId` (integer). |
| `delete_user_hwid_device(user_uuid, hwid)` (`:409`) | `POST /api/hwid/devices/delete` body `{userUuid, hwid}` | `DELETE /api/hwid/devices/delete` body `{userId, hwid}` | **HTTP verb POST → DELETE**, поле `userUuid` → `userId`. |
| `delete_all_user_hwid_devices(user_uuid)` (`:418`) | `POST /api/hwid/devices/delete-all` body `{userUuid}` | `DELETE /api/hwid/devices/delete-all` body `{userId}` | То же изменение. |

## 3. High-level модули — что менять

### `remnawave_premium.py`

| Функция | Действие |
|---|---|
| `create_premium_user_entity` (`:195`) | Preflight `find_user_by_username` — обновить path. POST body — вероятно не меняется. Проверить acceptance `vlessUuid` в 3.0. |
| `_ensure_premium_entity_state` (`:137`) | `update_user(expireAt, status="ACTIVE", externalSquadUuid)` — переходит на `PATCH /users/update`. Проверить field name `externalSquadUuid`. |
| `renew_premium_user` (`:376`) | Тот же PATCH — можно перевести на dedicated `POST /users/{id}/actions/extend` для чистоты, но PATCH продолжит работать. |
| `disable_premium_user` (`:442`) | `update_user(status="DISABLED")` → **лучше** переехать на dedicated `POST /users/{id}/actions/disable`. |
| `reissue_premium_user_entity` (`:463`) | `delete_user(old_uuid)` — обновить path (`DELETE /users/delete/{userId}`), нужен id. |
| `get_premium_subscription_url` (`:529`) | `get_user(rmn_uuid)` — path unchanged pattern, но нужен id/uuid определиться. |
| `_is_our_entity` (`:99`) | Логика matches без изменений (`telegramId`, description-маркеры). Но если панель добавит поле `id` — использовать его в кеше. |

### `remnawave_bypass.py`

| Функция | Действие |
|---|---|
| `create_bypass_user_entity` (`:148`) | То же что premium: preflight по новому path, POST body — прежний. |
| `add_bypass_traffic` (`:258`) | `get_user(rmn_uuid)` + `update_user(rmn_uuid, trafficLimitBytes=…, status="ACTIVE")`. PATCH переходит на `/users/update`. |
| `delete_bypass_user` (`:303`) | `DELETE /users/delete/{userId}`. |
| `_is_our_entity` (`:65`) | Уже есть username fallback (fix 62) — работает. |
| `_backfill_telegram_id` (`:117`) | PATCH `telegramId` — обновить path. |

### `remnawave_service.py` (legacy)

Все `*_bg` fire-and-forget — те же изменения, аналогично `remnawave_premium.py` и `remnawave_bypass.py`. Особое внимание:
- `add_bypass_traffic` (`:364`) — **A019 recovery** через `find_user_by_username`. Критично для fix'а `conflict_unrelated_user` — обновить path.
- `ensure_squad` (`:148`) — `assign_user_to_squad` fallback chain. Проверить актуальные endpoint'ы `/api/squads/*` в 3.0.

## 4. Cache columns — что делать

Наши колонки в `subscriptions`:
- `remnawave_uuid` (bypass) — TEXT
- `remnawave_premium_uuid` (premium) — TEXT
- `remnawave_premium_sub_url`, `remnawave_premium_short_uuid`
- `remnawave_bypass_sub_url`, `remnawave_bypass_short_uuid`

**Панель 3.0 всё ещё отдаёт `uuid` в response'ах** (для совместимости с subscription URLs — `sub.example.com/{shortUuid}`). Path-параметры в API — теперь `id` (integer).

**Стратегия:**
1. **НЕ УДАЛЯТЬ** existing UUID cache — панель продолжит его отдавать в response.
2. **ДОБАВИТЬ** новые колонки `remnawave_id` (INT), `remnawave_premium_id` (INT) через миграцию 076. Заполнять при adoption/create.
3. Все `update_user` / `delete_user` / `actions/*` — использовать `id` из кеша. Если `id` в кеше нет, но есть `uuid` — сделать fallback `by-username` lookup → получить `id` → закешировать.

Alternative (проще, менее чисто): продолжать использовать `uuid` как identifier в high-level коде, конвертировать в `id` внутри `remnawave_api.py` через кеш `{uuid: id}` per session.

## 5. Env vars — что нужно поменять

**Panel-side (только на самом хосте панели, не в боте):**
- В шаге 2.8.0 → 2.8.1: `JWT_AUTH_SECRET` → `APP_SECRET` в `.env` панели. **Обязательно ДО** запуска 2.8.1.

**Bot-side (у нас):**
- Ничего не меняется в env. Всё то же: `REMNAWAVE_API_URL`, `REMNAWAVE_API_TOKEN`, `REMNAWAVE_SQUAD_UUID`, `REMNAWAVE_MAIN_SQUAD_UUID`, паттерны и т.п.
- Опционально: добавить `REMNAWAVE_API_VERSION=3` feature flag для роутинга между старым и новым клиентом при переходе.

## 6. Background workers — impact

| Worker | Impact 3.0 | Действие |
|---|---|---|
| `traffic_monitor` (5min) | `get_user_traffic` работает через `get_user` — path unchanged pattern, нужен id/uuid определиться | Заменить путь / thread id через кеш |
| `auto_renewal` | `renew_remnawave_user_bg` → `update_user` PATCH | PATCH переходит на `/users/update` |
| `fast_expiry_cleanup` | `extend_remnawave_for_bypass_bg` → PATCH `expireAt` | Можно на dedicated `actions/extend` |
| `trial_notifications` | То же | То же |
| Admin `reconciliation` | `get_all_users(page_size=1000)` — 10+ страниц | Path переехал на `/users/get`. Опционально: `/users/stream` для scan'а. |
| Admin `recovery_premium` | `get_user` + `update_user` per user | Обновить пути |
| Admin `audit_subs` | `find_user_by_username` + `update_user` / `create_premium_user_entity` | Обновить `find_user_by_username` path |

## 7. Риски из audit'а — top 9 + план обхода

| # | Риск | План обхода |
|---|---|---|
| 1 | **`update_user` auto-discover cache** — залипнет на deprecated variant | Удалить `_update_method` cache при первом запуске под 3.0 или зафиксить canonical `PATCH /users/update`. Один вариант, без discover. |
| 2 | **`assign_user_to_squad` — 4 fallback** | То же: удалить fallback chain после проверки актуального endpoint'а в 3.0 backend contract. |
| 3 | **v2.7+ split `uuid`/`vlessUuid`/`shortUuid`** может снова смениться в 3.0 | Проверить на staging: `POST /users/create` с `vlessUuid` — принимает ли, возвращает ли поле. |
| 4 | **`activeInternalSquads`** может быть переименован в `internalSquadUuids` или `squads` | Проверить актуальный `libs/contract/models/users.model.ts` на GitHub. |
| 5 | **`externalSquadUuid`** (Task 6, override template) | Проверить field name в 3.0. |
| 6 | **Cursor-based pagination** в 3.0 (упомянуто в changelog v2.8) | `get_all_users` уже страничный (size+start), проверить работает ли в 3.0. |
| 7 | **`GET /users/by-username/{name}` → `/username/{name}`** | Простая замена path. Критично для preflight. |
| 8 | **HWID endpoints DELETE + `userId`** | Простая замена HTTP verb + rename поля. |
| 9 | **Response envelope `{"response": ...}`** может исчезнуть в 3.0 | Проверить одним curl'ом на staging — вернёт ли `{"response": {...}}` или сразу `{...}`. Если envelope убрали — правим `_request` / `_request_raw`. |

## 8. Полный чек-лист миграции нашего кода

### Обязательные изменения в `app/services/remnawave_api.py`
- [ ] `find_user_by_username`: `/by-username/` → `/username/`
- [ ] `create_user`: `POST /users` → `POST /users/create`
- [ ] `get_all_users`: `GET /users` → `GET /users/get`
- [ ] `get_user`: `GET /users/{uuid}` → `GET /users/{userId}` (+ id lookup)
- [ ] `update_user`: удалить `_update_method` auto-discover, зафиксить `PATCH /users/update` body-based
- [ ] `delete_user`: `DELETE /users/{uuid}` → `DELETE /users/delete/{userId}`
- [ ] `reset_user_traffic`: `POST /users/{uuid}/reset-traffic` → `POST /users/{userId}/actions/reset-traffic`
- [ ] `delete_user_hwid_device`: `POST /hwid/devices/delete` → `DELETE /hwid/devices/delete` + rename `userUuid` → `userId`
- [ ] `delete_all_user_hwid_devices`: то же
- [ ] `get_user_hwid_devices`: path parameter `userUuid` → `userId`
- [ ] `assign_user_to_squad`: убрать fallback chain после проверки контракта — оставить один канонический endpoint

### Опциональные (лучше сразу, для чистоты)
- [ ] `disable_premium_user`: перейти с `update_user(status=DISABLED)` на `POST /users/{userId}/actions/disable`
- [ ] `renew_premium_user`: перейти с `update_user(expireAt=…)` на `POST /users/{userId}/actions/extend` (dedicated body с days/date)
- [ ] Admin reconciliation: перейти с `get_all_users` на `GET /users/stream` для fast scan

### DB
- [ ] Миграция 076: `ALTER TABLE subscriptions ADD COLUMN remnawave_id INT, ADD COLUMN remnawave_premium_id INT`
- [ ] Backfill: при первом adopt/create после переключения — писать оба поля
- [ ] Helpers в `database/traffic.py`: `get_remnawave_id`, `set_remnawave_id`, аналоги для premium

### Config
- [ ] `config.py`: добавить `REMNAWAVE_API_VERSION` (default `2`, при переключении → `3`)
- [ ] Роутинг: если `REMNAWAVE_API_VERSION == 3` — использовать новые endpoint'ы; иначе — старые

### Тесты
- [ ] `tests/services/test_remnawave_api_find.py` — обновить mocked path `by-username/` → `username/`
- [ ] `tests/services/test_remnawave_service_add_bypass.py` — обновить A019 recovery mock
- [ ] Новые тесты на actions/* endpoints (enable, disable, reset, revoke, extend)
- [ ] E2E: create → renew → add-traffic → delete через новый клиент

## 9. Что проверить на staging ДО переключения

1. **Envelope** — GET `/api/users/get?size=1` — приходит `{response: {users: [...]}}` или `{users: [...]}`?
2. **`vlessUuid` acceptance** — POST `/api/users/create` с `vlessUuid=<uuid>` — в response `vlessUuid` есть? Форсинг работает?
3. **`activeInternalSquads` field** — POST с этим полем — принимает? Возвращает в response?
4. **`externalSquadUuid`** — то же.
5. **`by-username` deprecated или удалён?** — GET `/api/users/by-username/test` → 404? Или редиректит?
6. **`PATCH /users/{uuid}` (старый auto-discover)** — работает? Или уже 404?
7. **HWID `POST /hwid/devices/delete`** — старый POST — работает или 405 Method Not Allowed?
8. **Pagination**: `?size=1000&start=0` — работает как раньше?

Скрипт для проверки:
```bash
curl -H "Authorization: Bearer $TOKEN" $URL/api/users/get?size=1 | jq
curl -H "Authorization: Bearer $TOKEN" $URL/api/users/by-username/test  # 404 = deprecated, 200 = still works
curl -X POST -H "Authorization: Bearer $TOKEN" $URL/api/hwid/devices/delete -d '{"userId":1,"hwid":"x"}'  # проверить verb
```
