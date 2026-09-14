# Remnawave 3.4.3 — сверка интеграции бота

> Эталон: исходники `remnawave/backend` тег **3.4.3** (коммит `f8ad8ad`), `libs/contract` (zod-контракты) и
> `src/modules/**`. Сверка сделана 2026-09-14. Ссылки вида `file:line` ведут в этот тег.
> Разница 3.4.2→3.4.3: один фикс (`backend-tools auth bypass via mixed-case path`, `src/main.ts`), API-контракт
> пользователей, нод и статистики **не менялся**. Ничего из того, что вызывает бот, релиз не затронул.

Легенда: ✅ совпадает · ⚠️ работает, но есть оговорка · ❌ расходится (было исправлено или ждёт решения).

## 1. Транспорт, авторизация, конверт

| Пункт | 3.4.3 | Бот | |
|---|---|---|---|
| Авторизация | `Authorization: Bearer <API token>` | `_headers()` в `app/services/remnawave_api.py` | ✅ |
| Reverse proxy | `src/common/middlewares/proxy-check.middleware.ts:9-27`, подключён в `src/main.ts:126`. Вне dev без `X-Forwarded-For` **и** `X-Forwarded-Proto: https` сокет закрывается без ответа | заголовки не шлёт. Работает, пока `REMNAWAVE_API_URL` смотрит в HTTPS-прокси, который их добавляет. Прямой адрес бэкенда (`http://remnawave:3000`) даст обрыв соединения: status 0, всё «unavailable» | ⚠️ |
| Конверт успеха | `{ "response": ... }` (`commands/users/user.response.ts:5-7`) | `_request` / `_request_raw` снимают `response` | ✅ |
| DELETE | `204 No Content` (`users.controller.ts:115-117`) | `_request` возвращает `{}` на 202/204 | ✅ |
| Тело ошибки | `{timestamp, path, message, errorCode}` (`src/common/exception/http-exception.filter.ts:60-65`); ошибка zod: 400 с `errors` | `_request_raw` отдаёт тело в `body` | ✅ |
| Лишние поля в запросе | глобальный `ZodValidationPipe` (`src/main.ts:138`), `z.object` вырезает неизвестные ключи | бот шлёт `deviceLimit` (в 3.x его нет): поле тихо выбрасывается | ⚠️ P2 |
| Rate limit | **троттлера в бэкенде нет**: ни `@nestjs/throttler` в `package.json`, ни guard'а в `src/`. 429 может прийти только от прокси/CDN перед панелью | `get_all_users` делает backoff на странице; остальные вызовы без ретрая (`_request` → `None`) | ✅ |

## 2. Эндпоинты, которые вызывает бот

| Функция бота | Метод и путь | Поля запроса | Поля ответа, которые читает бот | Источник 3.4.3 | |
|---|---|---|---|---|---|
| `create_user` | `POST /api/users` → 201 | `username`, `shortUuid`, `trafficLimitBytes`, `trafficLimitStrategy=NO_RESET`, `status=ACTIVE`, `expireAt`, `hwidDeviceLimit`, `vlessUuid`, `description`, `telegramId`, `externalSquadUuid`, `activeInternalSquads:[uuid]`, `tag` (§3a) (+ лишний `deviceLimit`) | `id`, `vlessUuid`, `shortUuid`, `subscriptionUrl`, `activeInternalSquads` | `commands/users/create-user.command.ts:18-120`, `users.controller.ts:87` | ✅ |
| `update_user` | `PATCH /api/users` (id в теле) → 200 | `id` + `trafficLimitBytes`, `expireAt`, `status`, `telegramId`, `externalSquadUuid`, `hwidDeviceLimit`, `tag` (§3a) (+ лишний `deviceLimit` в `renew_remnawave_user`) | весь `ExtendedUser` (проверяется только `is not None`) | `update-user.command.ts:18-61`, `users.controller.ts:101` | ⚠️ ограничения PATCH, §4 |
| `get_user` / `_entity_state` | `GET /api/users/{userId}` (**числовой** id) → 200 / 404 | — | `id`, `username`, `telegramId`, `vlessUuid`, `shortUuid`, `trafficLimitBytes`, `expireAt`, `status`, `subscriptionUrl`, `userTraffic.usedTrafficBytes`, `activeInternalSquads`, `hwidDeviceLimit` | `get-user-by-id.command.ts:19-21` (`numberParamSchema` = `z.coerce.number().positive()`), `users.controller.ts:247` | ✅ |
| `find_user_by_username`, `find_user_by_short_uuid`, `_entity_state` | `POST /api/users/resolve` → 200 / 404 | ровно одно из `{id \| shortUuid \| username}` | **только** `{id, username, shortUuid}` → бот дочитывает `GET /api/users/{id}` | `resolve-user.command.ts:18-42`, `users.service.ts:817-828` (`USER_NOT_FOUND` 404) | ❌→✅ F1 |
| `find_user_by_telegram_id`, `_resolve_to_int_id`, `get_all_users` | `GET /api/users/stream?size&cursor&telegramId` | `size` 1..1000 (по умолчанию 250), `cursor` = прошлый `nextCursor`, фильтры: `status`, `trafficLimitStrategy`, `telegramId`, `email`, `tag`, `externalSquadUuid`. **Фильтра `username` нет** | `users[]`, `nextCursor` (**string \| null**), `hasMore` (поля `total` нет) | `get-users-stream.command.ts:18-59`, `users.controller.ts:154` | ⚠️ F10 |
| `delete_user` | `DELETE /api/users/{userId}` → 204 | — | — | `delete-user.command.ts`, `users.controller.ts:115` | ✅ |
| `database/admin.py:4636` (сырой `_request`) | `DELETE /api/users/delete/{id}` | — | — | такого маршрута в 3.x нет → 404 | ❌ F4 |
| `revoke_user_subscription` (вызовов нет) | `POST /api/users/{userId}/actions/revoke` | тело необязательно (`revokeOnlyPasswords`, `shortUuid` 16..64) | — | `revoke-user-subscription.command.ts:19-44` | ✅ |
| `assign_user_to_squad` | `POST /api/internal-squads/{uuid}/bulk-actions/add-many-users` → **202** (в очередь) | `userIds: number[]` 1..1000 | — | `add-many-users-to-internal-squad.command.ts:17-23`, `internal-squad.controller.ts:262-263` | ✅ (фолбэк `PATCH activeInternalSquads` **заменяет** список сквадов) |
| `get_user_hwid_devices` | `GET /api/hwid/devices/{userId}` | — | `devices[]` (`hwid`, `platform`, `deviceModel`, …), `total` | `get-user-hwid-devices.command.ts:18-27`, `hwid-user-devices.controller.ts:174` | ✅ |
| `delete_user_hwid_device` | `POST /api/hwid/devices/delete` | `{userId, hwid}` | — | `delete-user-hwid-device.command.ts:11-21`, `hwid-user-devices.controller.ts:99` | ✅ (DELETE-фолбэк лишний) |
| `delete_all_user_hwid_devices` | `POST /api/hwid/devices/delete-all` | `{userId}` | — | `delete-all-user-hwid-devices.command.ts:11-20`, `hwid-user-devices.controller.ts:121` | ✅ |
| `get_system_stats` | `GET /api/system/stats?tz=` | `tz` | `users.statusCounts{ACTIVE,DISABLED,LIMITED,EXPIRED}`, `users.totalUsers`, `onlineStats.*`, `nodes.totalOnline`, `nodes.totalBytesLifetime` (string) | `get-stats.command.ts:18-49`, `system.controller.ts:102` | ✅ |
| `get_bandwidth_stats` | `GET /api/system/stats/bandwidth?tz=` | `tz` | `bandwidthLast{TwoDays,SevenDays,30Days}`, `bandwidthCalendarMonth`, `bandwidthCurrentYear` | `get-bandwidth-stats.command.ts:18-30`, `system.controller.ts:116` | ✅ |
| `get_nodes` | `GET /api/nodes` | — | `uuid`, `name`, `countryCode`, `isConnected`, `isDisabled`, `isConnecting`, `lastStatusMessage`, `lastStatusChange`, `usersOnline`, `trafficUsedBytes`, `trafficLimitBytes`, `xrayUptime`, `versions`, `provider.name`, `tags` | `models/nodes.schema.ts:8-54`, `nodes.controller.ts:95` | ✅ |
| `get_nodes_metrics` | `GET /api/system/nodes/metrics` | — | `nodes[].nodeUuid`, `providerName` | `get-nodes-metrics.command.ts:17-43`, `system.controller.ts:160` | ✅ |
| `get_nodes_usage` | `GET /api/bandwidth-stats/nodes?start&end&topNodesLimit` | даты `YYYY-MM-DD` | `categories`, `sparklineData`, `series[].{name,countryCode,total,data}` | `get-stats-nodes-usage.command.ts:17-51`, `nodes-usage-history.controller.ts:30` | ✅ |
| `get_hwid_stats` | `GET /api/hwid/devices/stats` | — | `stats.{totalUniqueDevices,totalHwidDevices,averageHwidDevicesPerUser}`, `byPlatform[]` | `get-hwid-devices-stats.command.ts:17-37`, `hwid-user-devices.controller.ts:140` | ✅ |
| дашборд `reset-premium-unlimited` (сырой `_request`) | `GET /api/users/{id}`, `PATCH /api/users` | `{id, trafficLimitBytes:0, trafficLimitStrategy:NO_RESET, status:ACTIVE}` | `trafficLimitBytes`, `status` | как выше | ✅ |

В 3.4.3 **есть** `GET /api/users/by-username/{username}` и `GET /api/users/by-short-uuid/{shortUuid}`: они отдают полный
`ExtendedUser` за один запрос (`users.controller.ts:231,263`, `USERS_ROUTES.GET_BY`). Докстринги бота называли их
удалёнными. Бот ходит через `/resolve` + `GET` по id, то есть делает два запроса вместо одного.

## 3. Модель пользователя (ответ)

`ExtendedUsersSchema` = `UsersSchema` + `subscriptionUrl`, `activeInternalSquads`, `userTraffic`
(`models/users.schema.ts:5-27`, `models/extended-users.schema.ts:7-11`, `models/user-traffic.schema.ts:3-9`).

| Поле | Тип | Что делает бот |
|---|---|---|
| `id` | number | ключ всех path-параметров, кешируется в `remnawave_id` / `remnawave_premium_id` ✅ |
| `uuid` | **нет в 3.x** | везде читается как `get("uuid") or get("vlessUuid")` ✅; в колонки `remnawave_uuid` / `remnawave_premium_uuid` фактически пишется `vlessUuid` |
| `vlessUuid` | uuid | принимается при создании (`vlessUuid` в теле), по нему бот отличает «свой forced uuid» ✅ |
| `shortUuid`, `subscriptionUrl` | string | `subscriptionUrl` = ссылка подписки; хост переписывает `rewrite_sub_host` на стороне бота ✅ |
| `status` | `ACTIVE \| DISABLED \| LIMITED \| EXPIRED` (`constants/users/status/status.constant.ts:1-6`) | ✅ |
| `trafficLimitBytes` | number, `0` = безлимит | premium = 0, bypass = накопленные байты ✅ |
| `trafficLimitStrategy` | `NO_RESET \| DAY \| WEEK \| MONTH \| MONTH_ROLLING` (`reset-periods.constant.ts:1-7`) | всегда `NO_RESET` ✅ |
| `expireAt` | ISO-строка, UTC, с миллисекундами (`2025-01-17T15:38:45.065Z`) | парсится `fromisoformat(... "Z"→"+00:00")` ✅; на запись идёт `%Y-%m-%dT%H:%M:%SZ`, zod `iso.datetime({offset, local})` это принимает ✅ |
| `userTraffic.usedTrafficBytes` | number | расход читается **только отсюда**, верхнеуровневого `usedTrafficBytes` нет. `get_user_traffic`, `panel_traffic_audit` читают правильно; `disable_remnawave_user` читал верхний уровень ❌→✅ F6 |
| `hwidDeviceLimit` | `int \| null` | `get_user_traffic` возвращает `deviceLimit=None`, если в панели null ⚠️ |
| `onlineDevices` | **нет в 3.x** | `get_user_traffic` всегда отдаёт `0` (использует только админский экран трафика) ⚠️ F9 |
| `activeInternalSquads` | `[{uuid, name}]` (объекты, не строки) | бот проверяет только «пусто/не пусто» ✅ |
| `telegramId`, `description`, `externalSquadUuid` | nullable | ✅ |

Ограничения на запись (`create-user.command.ts:19-29`): `username` — `^[a-zA-Z0-9_-]+$`, длина 3..36. Шаблоны бота:
`{tg_id}` (bypass) и `tg_{tg_id}_premium` (premium), обрезка до 32 символов ✅. `shortUuid` при создании без
ограничений; бот шлёт `str(uuid4())[:12]` (с дефисом) ✅. `tag` — `^[A-Z0-9_]+$` до 16 символов (бот шлёт с 2026-09-14, §3a).

## 3a. Тег пользователя (`tag`): теги по тарифу (2026-09-14)

Решение владельца: тег premium-сущности = текущий тариф, тег bypass-сущности = `BYPASS`. Сверено по тегу 3.4.3:

| Что | 3.4.3 | Источник |
|---|---|---|
| Создание | `tag` в теле `POST /api/users`: `z.optional(z.string().regex(/^[A-Z0-9_]+$/).max(16).nullable())` | `libs/contract/commands/users/create-user.command.ts:81-95`; пишется как есть: `src/modules/users/users.service.ts:89` |
| Изменение | `tag` в теле `PATCH /api/users`: тот же regex и длина, `nullable` (`null` снимает тег) | `libs/contract/commands/users/update-user.command.ts:41-50`; попадает в сущность через `...rest`: `src/modules/users/users.service.ts:138-163` |
| Одно значение | у пользователя одна строка или `null`, не массив | `libs/contract/models/users.schema.ts:16` |
| Фильтр | `GET /api/users/stream?tag=` (точное совпадение) | `libs/contract/commands/users/get-users-stream.command.ts:46`, `src/modules/users/repositories/users.repository.ts:279-280` |
| Список тегов | `GET /api/users/tags` | `libs/contract/commands/users/tags/get-users-tags.command.ts:7`, `libs/contract/api/controllers/users.ts:41-42` |
| Неверный тег | 400 от zod с `path: ["tag"]` (глобальный `ZodValidationPipe`, `src/main.ts:138`) | |

Что делает бот:

| Сущность | Тег | Где ставится |
|---|---|---|
| premium | `TRIAL` (пробный и подарочные 3 дня за покупку ГБ), `BASIC`, `PLUS` (и легаси biz), `COMBO_BASIC`, `COMBO_PLUS` — `tariffs.premium_panel_tag` / `premium_panel_tag_for_subscription` | outbox: `provisioning._apply_premium` (POST при создании; в PATCH продления, если тег другой; при смене тарифа без переноса даты — один PATCH тега, только если у сущности уже есть **другой** тег). Старый путь: `purchase_flow.provision_subscription` / `_sync_renewal_once` → `remnawave_premium.create_premium_user_entity` / `renew_premium_user` |
| bypass | `BYPASS` | `remnawave_bypass.create_bypass_user_entity` (POST), PATCH пополнения ГБ (`provisioning._cas_bypass`, `add_bypass_traffic`, старые `remnawave_service.*`), если тег другой |

- Выдачи днями (админ, игра, промо) тег не меняют: у outbox-джобы `grant` тег ставится только новой сущности, старый путь передаёт `keep_panel_tag`. По истечении тег остаётся (статус `EXPIRED` в панели и так виден).
- Лишних запросов нет: тег едет в POST/PATCH, которые бот делает и так. Продление тем же тарифом отправляет тот же PATCH (outbox без поля `tag`).
- Тег не может сломать покупку: невалидный тег не отправляется (`remnawave_api.clean_tag`), а 400, где упомянут `tag`, повторяется один раз тем же запросом без тега (`remnawave_api._send_tagged`, лог `REMNAWAVE_TAG_REJECTED`). Срок и ГБ всё равно выдаются.
- Существующие пользователи получают теги **только бэкфиллом** по кнопке админа. Он одобрен владельцем для пользователей с активной подпиской и меняет только теги. Запуск: дашборд → «Ещё» → «Настройки» → «Теги в панели Remnawave» (проверка без изменений, затем «Проставить теги» → подтверждение; «Пауза», «Продолжить», «Стоп»), либо `python -m scripts.backfill_remnawave_tags` (по умолчанию dry-run; `--apply` — прогон, `--limit N` — ограничение числа PATCH). Код: `app/services/remnawave_tags`. PATCH только тега `{id, tag}`, не больше 2 в секунду, только у сущностей с другим тегом. Тег перечитывается из БД перед каждой пачкой из 20 сущностей. Прогресс хранится в `app_settings` (`remnawave_tag_backfill`). После перезапуска бота задача видна как «прервано», «Продолжить» доделывает остаток (уже верные теги пропускаются). По завершении приходит алерт админу.

## 4. Поведение, на которое опирается бот

| Предположение | Факт 3.4.3 | |
|---|---|---|
| Панель сама переводит в `EXPIRED` / `LIMITED` | да: `FIND_EXPIRED_USERS` каждые 30 с, `FIND_EXCEEDED_TRAFFIC_USAGE_USERS` каждые 45 с (`src/scheduler/intervals.ts`); `LIMITED` ставится при `used >= limit` и `limit != 0` (`users.repository.ts:146-152`) | ✅ |
| PATCH `status` | принимает **только** `ACTIVE \| DISABLED` (`update-user.command.ts:22`); `EXPIRED`/`LIMITED` → 400. Бот шлёт только ACTIVE/DISABLED | ✅ |
| PATCH `status=ACTIVE` на LIMITED/EXPIRED | ставит ACTIVE безусловно (`users.service.ts:173-176`); если лимит или срок всё ещё исчерпан, сторож вернёт статус через 30–45 с. Бот всегда шлёт ACTIVE вместе с новым лимитом/сроком | ✅ |
| PATCH `trafficLimitBytes` на LIMITED без status | авто-ACTIVE, если новый лимит больше старого или 0 (`users.service.ts:183-192`) | ✅ |
| PATCH `expireAt` на EXPIRED без status | авто-ACTIVE, если дата в будущем (`users.service.ts:195-204`) | ✅ |
| **PATCH `expireAt` в прошлом** | **отклоняется 400** «Expiration date cannot be in the past» (`update-user.command.ts:32-39`). POST это ограничение не проверяет | ⚠️ F7 |
| PATCH с более ранним `expireAt` | разрешён, если дата в будущем: срок **можно укоротить**. Ядро provisioning не шлёт PATCH, если `panel >= target` | ✅ |
| PATCH по uuid | нет: идентификатор — `id` **или** `username` в теле (`update-user.command.ts:59-61`). Бот резолвит uuid → id | ✅ |
| Дубль username | **HTTP 400, `errorCode A019`** (`errors.ts:69-73`, `users.service.ts:115-124`), не 409 | ❌→✅ F2 |
| «Не найдено» | GET по id → 404 `A063` (`errors.ts:314-318`, `users.service.ts:295-303`); resolve / update / delete → 404 `A025` (`errors.ts:99-103`) | ✅ |
| Неверный токен | 401 | ✅ (unavailable) |

## 5. Классификация present / absent / unavailable

`_entity_state` (`remnawave_api.py`): 404 или 4xx с «not found» → absent; 0 (транспорт/обрыв прокси), 5xx, 401, 403, 408, 429
→ unavailable; прочие 4xx (400 валидации) → unavailable. На 3.4.3 это верно: «не найдено» у resolve и GET по id — только 404.
Дубль сущности невозможен и при ошибочном «absent»: username уникален, POST вернёт A019, после F2 бот адоптирует сущность.
Одна оговорка: 404 от неверного `REMNAWAVE_API_URL` или прокси тоже читается как absent. Тогда create падает тем же 404,
provisioning ретраит, дубля нет.

## 6. Нагрузка

Троттлера в панели нет. Объём запросов бота:
- **покупка** (ядро provisioning): premium read (1–2: GET по id, при промахе resolve + GET), PATCH (0–1), bypass read (1–2), PATCH/POST (0–1), verify (2–4). Итого ≈ 4–10 запросов;
- **`get_all_users`** (сверка, дашборд): страницы по 250, backoff до 30 с на страницу, без вложенного ретрая;
- **дашборд**: `system/stats`, `bandwidth`, `hwid/stats`, `nodes`, `nodes/metrics`, `bandwidth-stats/nodes` — кеш `PANEL_TTL_SECONDS` (`app/services/panel_stats.py`), короткий таймаут `_READ_TIMEOUT`;
- **`reset-premium-unlimited`**: до 10 параллельных GET/PATCH;
- **retry-в-retry нет**: `renew_premium_user` (3 попытки) и ретраи provisioning-джобы относятся к разным путям (старый путь и outbox) и не вложены друг в друга.

## 7. Находки

| # | Сев. | Суть | Доказательство | Статус |
|---|---|---|---|---|
| F1 | **P0** | `find_user_by_username`: если `GET /api/users/{id}` после `/resolve` падал, возвращался урезанный `{id, username, shortUuid}`. `get_bypass_entity_safe` отдавал его в `add_bypass_traffic` / `add_traffic`, те читали `trafficLimitBytes=0` и PATCH'или лимит = только купленные байты. **Накопленные ГБ стирались** | `resolve-user.command.ts:36-42` | исправлено: неполная сущность = «не найдено» |
| F2 | P1 | Гонка «preflight не увидел → POST → дубль» ловилась по 409, а 3.4.3 отвечает 400 A019. Адопция не срабатывала; premium ещё и повторял POST без `vlessUuid` (снова A019). Итог: выдача падала вместо адопции. После F1 (неполная сущность → None) эта ветка встречается чаще | `errors.ts:69-73`, `users.service.ts:115-124`, `http-exception.filter.ts:60-65` | исправлено: `remnawave_api.is_username_conflict` |
| F3 | P1 | `_resolve_to_int_id`: uuid без закешированного id резолвился через `stream?telegramId=X`, бралась **первая** сущность. У bypass и premium один telegramId, поэтому PATCH/DELETE мог уйти в чужую сущность | `get-users-stream.command.ts:39-44` (фильтр по telegramId возвращает все совпадения) | исправлено: выбор по `vlessUuid`, при неоднозначности `None` |
| F4 | P1 | `database/admin.py:4636` удаляет сущности через `DELETE /api/users/delete/{id}`. В 3.x такого пути нет (404, `quiet=True`): полное удаление юзера админом оставляет обе сущности в панели. Следующая покупка адоптирует их со старыми ГБ и сроком | `api/controllers/users.ts` `DELETE: (userId) => \`${userId}\`` | **владельцу**: одна строка, `remnawave_api.delete_user(target_id)`; не трогал, админка сейчас удаляется параллельно |
| F5 | P2 | Откат перевыпуска (`database/subscriptions.py:1003`) удаляет новую premium-сущность через `delete_user(uuid)`. Этого uuid ещё нет в БД, резолв не находит id, сущность не удаляется, хотя в лог пишется `ORPHAN_PREVENTED`. Следующий перевыпуск адоптирует сироту и падает с `reissue_no_rotation` | `resolve`/`GET` требуют числовой id (`get-user-by-id.command.ts:19-21`) | **владельцу**: удалять по `reissue_result.panel_id` (путь админки) |
| F6 | P2 | `disable_remnawave_user` читал верхнеуровневый `usedTrafficBytes` (в 3.x его нет), получал 0 и никогда не отключал bypass с израсходованными ГБ | `user-traffic.schema.ts:3-9` | исправлено |
| F7 | P2 | PATCH с `expireAt` в прошлом → 400. Провизионинг шлёт `max(premium_until, expires_at)`: если джоба обрабатывается уже после окончания оплаченного срока, PATCH падает, джоба ретраится до алерта. Доступ не теряется: срок уже истёк. Админские `audit_subs` / `recovery_premium` тоже могут прислать прошлую дату | `update-user.command.ts:32-39` | **владельцу**: решить, что делать с «опоздавшей» выдачей (пропуск или алерт) |
| F8 | P2 | В `POST /api/users` и `PATCH` (`renew_remnawave_user`) уходит `deviceLimit`. В 3.x поля нет, zod его вырезает. Мёртвый ключ, нужен только `hwidDeviceLimit` | `create-user.command.ts:105-112`, `src/main.ts:138` | исправлено: ключ убран |
| F9 | P2 | `get_user_traffic` / `get_bypass_traffic_safe` отдают `onlineDevices` (поля в 3.x нет, всегда 0) и `deviceLimit=None` при `hwidDeviceLimit: null`. Потребитель — админский экран трафика | `users.schema.ts:16`, отсутствие `onlineDevices` в `extended-users.schema.ts` | не критично; устройства брать из `/api/hwid/devices/{id}` (`total`) |
| F10 | P2 | `get_all_users`: `nextCursor` — **строка**, поля `total` нет (есть `hasMore`). Курсор передаётся как есть, поэтому пагинация работает; `progress_cb` всегда получает `total=None`; ретраится и 4xx | `get-users-stream.command.ts:50-58` | докстринг исправлен |
| F11 | P2 | `get_user(uuid)` без закешированного id делает два заведомо пустых запроса: `resolve {shortUuid: <UUID>}` → 404 и `GET /api/users/<UUID>` → 400 | `numberParamSchema` | оптимизация, не баг |
| F12 | P2 | Бот не шлёт `X-Forwarded-*`. Если `REMNAWAVE_API_URL` направить мимо HTTPS-прокси, панель рвёт соединение на каждом запросе | `proxy-check.middleware.ts:14-24` | **владельцу**: держать URL за прокси (как сейчас) |
| F13 | P2 | `REMNAWAVE_PREMIUM_USERNAME_PATTERN` с `{existing_username}` делает username недетерминированным: preflight по username перестаёт находить сущность, возможен дубль. По умолчанию шаблон `tg_{telegram_id}_premium`, это безопасно | `remnawave_premium.build_premium_username` | не менять шаблон в проде |
| F14 | P2 | Фолбэк `assign_user_to_squad` (`PATCH activeInternalSquads=[squad]`) **заменяет** список сквадов, а не добавляет. Основной путь `add-many-users` отвечает 202 и выполняется асинхронно | `update-user.command.ts:54`, `internal-squad.controller.ts:262-263` | допустимо (у сущностей один сквад) |

## 8. Вердикт по областям

| Область | Вердикт |
|---|---|
| Users CRUD | ✅ пути, методы, числовой id, тело PATCH с `id`, 201/200/204 совпадают. Лишний `deviceLimit` убран |
| Premium / bypass сущности | ✅ после F1–F3 (урезанный resolve, A019, выбор сущности по `vlessUuid`). Username и `expireAt` валидны |
| Трафик | ✅ байты, `0` = безлимит, `NO_RESET`, расход из `userTraffic.usedTrafficBytes` (F6 исправлен), авто-LIMITED/ACTIVE подтверждены |
| Stream / пагинация | ⚠️ работает; `nextCursor` строка, `total` нет, фильтра `username` нет (бот правильно использует `/resolve`) |
| Ноды / статистика | ✅ все поля дашборда есть в контрактах 3.4.3 |
| Ссылки подписки | ✅ `subscriptionUrl` в полном ответе. В `/resolve` его нет, бот дочитывает по id |
| Ошибки / лимиты | ✅ классификация present/absent/unavailable корректна; троттлера нет; retry-в-retry нет. ⚠️ `expireAt` в прошлом → 400 (F7) |

## 9. Что поменялось в коде (эта сверка)

- `remnawave_api.is_username_conflict` + адопция по 400 A019 в `create_premium_user_entity` / `create_bypass_user_entity` (F2).
- `find_user_by_username`: дочитывание по любому недостающему ключу (`id`, `trafficLimitBytes`, `expireAt`, `subscriptionUrl`), неполная сущность → `None` (F1).
- `_resolve_to_int_id`: выбор сущности по `vlessUuid` (F3).
- `disable_remnawave_user`: расход из `userTraffic` (F6).
- `deviceLimit` убран из тела create/renew, докстринги модуля приведены к 3.4.3 (F8, F10).
- `tests/fakes/panel.py`: форма ответа 3.4.3 (нет `uuid`, есть `userTraffic`), PATCH отклоняет то же, что панель (статусы кроме ACTIVE/DISABLED, прошлый `expireAt`, отрицательный лимит).
- 2026-09-14: premium с `expireAt` +10 лет. Старые bypass-хелперы `remnawave_service` (extend / disable / renew / ensure_squad / delete) искали bypass через кеш `remnawave_id` / `remnawave_uuid`. У заражённых строк этот кеш указывал на premium. Теперь они работают только с сущностью `username == str(telegram_id)` (`get_bypass_entity_safe`), а кеш перезаписывает `_heal_bypass_cache`, в том числе после `REMNAWAVE_BYPASS_STATE_CACHE_MISMATCH` в `get_bypass_state`. `create_user` / `update_user` не отправляют premium `expireAt` дальше `PREMIUM_MAX_EXPIRE_AHEAD` (5 лет): лог `REMNAWAVE_PREMIUM_FAR_EXPIRE_BLOCKED`, алерт `vpn_api`. SQL для подсчёта заражённых строк: `docs/RUNBOOK.md` §7 п.15.
- 2026-09-14: теги по тарифу (§3a). `tag` в `create_user` / `update_user`, `clean_tag`, фолбэк без тега, `set_user_tag`; фейки `tests/fakes/panel.py` и `tests/fakes/remnawave_http.py` валидируют `tag` как 3.4.3 (regex, ≤ 16, `null` на PATCH), флаг `reject_tags`, фильтр `stream?tag=`.
