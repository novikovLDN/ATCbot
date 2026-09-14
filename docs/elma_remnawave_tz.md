# ТЗ: интеграция Remnawave-панели для Elma-бота

> **Контекст для агента-исполнителя.** Это задание — реализовать в Elma-боте
> провизию VPN-подписок через ту же Remnawave-панель, что и Atlas Secure,
> но с упрощённой моделью: один продукт, один сервер (MainSquad), один
> entity на пользователя (как премиум у Atlas), безлимитный трафик.
>
> Префикс username в панели — `elma_<telegram_id>` (изоляция от Atlas).

---

## 1. Что нужно реализовать

Сервис, который умеет 4 операции с пользователем в Remnawave:

| Операция | Когда вызывать | API-метод |
|---|---|---|
| **create** | Активация триала / первая покупка | `POST /api/users` |
| **renew**  | Продление подписки               | `PATCH /api/users` |
| **find**   | Восстановление маппинга после сбоя БД | `GET /api/users/by-username/{username}` |
| **delete** | Отзыв доступа админом / по истечении | `DELETE /api/users/{uuid}` |

Идея архитектуры: бот ходит в панель сам, никаких webhook'ов «панель→бот» не нужно. Никакого разделения basic/plus — один тариф, всё в MainSquad.

---

## 2. Username и идентификация в панели

**Формат:** `elma_<telegram_id>` (Remnawave ограничивает username 32 символами — Telegram ID помещается с запасом).

```python
def build_username(telegram_id: int) -> str:
    return f"elma_{telegram_id}"[:32]
```

**Почему именно так:**
- Префикс `elma_` изолирует наши записи от других ботов на той же панели (у Atlas Secure уже занят префикс `tg_<id>_premium` — нельзя пересекаться, иначе перезатрём чужих пользователей).
- Один пользователь = одна запись в панели (telegram_id уникальный). Если запись уже есть с этим username — это **наша запись из прошлого запуска**, надо её адоптировать, а не создавать новую.

---

## 3. Структура запроса `POST /api/users` (create)

```json
{
  "username": "elma_5723947899",
  "shortUuid": "<12-symbol random>",
  "vlessUuid": "<full uuid v4, optional>",
  "trafficLimitBytes": 0,
  "trafficLimitStrategy": "NO_RESET",
  "status": "ACTIVE",
  "expireAt": "2026-12-31T23:59:59Z",
  "deviceLimit": 5,
  "description": "Elma bot subscription",
  "telegramId": 5723947899,
  "activeInternalSquads": ["<REMNAWAVE_MAIN_SQUAD_UUID>"]
}
```

**Важно:**
- `trafficLimitBytes: 0` = безлимит (это правило панели для премиум-юзеров).
- `expireAt` — ISO-8601 в UTC, с буквой `Z` на конце (НЕ `+00:00`, иначе панель ругается). Пример: `2026-12-31T23:59:59Z`.
- `vlessUuid` опционален. Если хочешь сохранить тот же VLESS-UUID при пере-создании пользователя (например, после потери записи в БД) — передавай его, и пользователь не будет перенастраивать клиента. Если генерируешь нового — оставь поле пустым, панель присвоит сама.
- `uuid` в ответе — это **panel-internal ID** (для последующих PATCH/DELETE). Сохрани его в БД, не путай с `vlessUuid`.
- `activeInternalSquads` — массив с одним squad'ом. Если поле проигнорировать, юзер не попадёт ни в один inbound и подписка работать не будет.

**Ответ панели:**
```json
{
  "uuid": "<panel-internal-uuid>",
  "vlessUuid": "<the one used in VLESS strings>",
  "shortUuid": "<short>",
  "subscriptionUrl": "https://rmnw.example.com/sub/<short>",
  "status": "ACTIVE",
  ...
}
```

Сохрани в БД пользователя: `panel_uuid`, `vless_uuid`, `subscription_url`.

---

## 4. Структура запроса `PATCH /api/users` (renew)

Remnawave у нас PATCH'ит ОДНИМ endpoint'ом — `/api/users` без uuid в path, с uuid в body:

```json
{
  "uuid": "<panel-internal-uuid>",
  "expireAt": "2027-12-31T23:59:59Z",
  "status": "ACTIVE"
}
```

Если у пользователя истёк срок и статус стал `EXPIRED`, передача `status: ACTIVE` снова его активирует. Это правильно — мы продлеваем подписку, юзер должен снова получить трафик.

---

## 5. Структура запроса `GET /api/users/by-username/{username}` (find)

Используется в одном сценарии: при создании пользователя сначала проверяем, нет ли его уже в панели (с прошлого запуска, который упал на полпути).

```
GET /api/users/by-username/elma_5723947899
```

Ответы:
- `200` + JSON юзера → **адоптируем**: достаём `uuid`, сохраняем в БД, при необходимости PATCH'им `expireAt`.
- `404` → пользователя нет, идём в `POST /api/users`.

---

## 6. Алгоритм `create_or_renew_subscription(telegram_id, expire_at)`

Самый важный кусок логики — идемпотентный:

```
1. username = "elma_" + telegram_id
2. Если в БД у telegram_id уже есть panel_uuid:
   2a. PATCH /api/users {uuid, expireAt, status: ACTIVE}
   2b. Если PATCH вернул 404 (запись удалена в панели) — обнулить panel_uuid в БД, перейти к шагу 3.
   2c. Если PATCH успешен → return.
3. Preflight: GET /api/users/by-username/{username}
   3a. Если найден — взять его uuid, сохранить в БД, PATCH {expireAt, status: ACTIVE}, return.
4. POST /api/users со всеми полями (см. п. 3).
   4a. Если 200/201 — сохранить uuid/vlessUuid/subscriptionUrl в БД, return.
   4b. Если 409 (username conflict) — повторить шаг 3 (race condition: запись появилась между preflight и POST).
   4c. Если другая ошибка — log + raise (наверх по цепочке упадёт alert).
```

**Атомарность:** PATCH/POST в панель И запись в БД должны быть в одной логической транзакции. Простейшая схема:
- Сначала PATCH/POST в панель.
- На успех — `UPDATE users SET panel_uuid=$1, vless_uuid=$2, subscription_url=$3, expires_at=$4 WHERE telegram_id=$5`.
- Если шаг с панелью упал — НЕ записывать в БД, дать ошибке всплыть наружу. Платёж останется в статусе `pending`, webhook ретраит — попадём сюда снова, попадём в шаг 3 (find by username), увидим адоптированную запись, продлим.

---

## 7. Структура БД

Минимум — одна таблица `subscriptions` (можно слить с `users`, как удобно):

```sql
CREATE TABLE subscriptions (
    telegram_id      BIGINT PRIMARY KEY,
    panel_uuid       TEXT,                  -- внутренний uuid в Remnawave (для PATCH/DELETE)
    vless_uuid       TEXT,                  -- UUID, который видит клиент в VLESS-ссылке
    subscription_url TEXT,                  -- готовая ссылка с панели, отдаём пользователю
    expires_at       TIMESTAMPTZ NOT NULL,
    status           TEXT NOT NULL,         -- 'active' | 'expired' | 'pending'
    source           TEXT NOT NULL,         -- 'trial' | 'payment' | 'admin'
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    activated_at     TIMESTAMPTZ DEFAULT NOW(),
    reminder_24h_sent BOOLEAN DEFAULT FALSE,
    reminder_3h_sent  BOOLEAN DEFAULT FALSE
);
```

**Везде используй `TIMESTAMPTZ`**, никогда не наивный `TIMESTAMP` — на наивных колонках ломается сравнение с `NOW()` (видели на Atlas Secure: «operator does not exist: timestamp without time zone > timestamp with time zone»).

---

## 8. Обёртка над HTTP (минимальный код)

Один HTTP-клиент с базовыми ретраями. Аутентификация — Bearer-токен в header'е.

```python
# app/services/remnawave.py
import httpx
import config

_BASE = config.REMNAWAVE_URL.rstrip("/")
_HEADERS = {"Authorization": f"Bearer {config.REMNAWAVE_TOKEN}"}


async def _req(method: str, path: str, **kwargs) -> dict:
    url = f"{_BASE}{path}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.request(method, url, headers=_HEADERS, **kwargs)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()


async def create_user(payload: dict) -> dict:
    return await _req("POST", "/api/users", json=payload)


async def update_user(panel_uuid: str, **fields) -> dict:
    return await _req("PATCH", "/api/users", json={"uuid": panel_uuid, **fields})


async def find_user_by_username(username: str) -> dict | None:
    return await _req("GET", f"/api/users/by-username/{username}")


async def delete_user(panel_uuid: str) -> None:
    await _req("DELETE", f"/api/users/{panel_uuid}")
```

Сверху делается тонкий слой `subscription_service.create_or_renew(...)` с логикой из п. 6.

---

## 9. Сценарии бота, которые дёргают этот сервис

| Триггер | Вызов |
|---|---|
| Юзер активировал триал | `create_or_renew(tg_id, expire_at = now() + 3 days)`, `source='trial'` |
| Юзер оплатил подписку | `create_or_renew(tg_id, expire_at = max(current, now()) + 30 days)`, `source='payment'` |
| Админ выдал N дней руками | `create_or_renew(tg_id, expire_at = current + N days)`, `source='admin'` |
| Подписка истекла (scheduler) | `delete_user(panel_uuid)` + `UPDATE subscriptions SET status='expired'` |

Bot UI получает `subscription_url` из БД и показывает пользователю как «кнопка подключиться» / «ссылка для импорта в Happ». Никаких VLESS-строк собственноручно не клеим — `subscription_url` от панели уже самодостаточна.

---

## 10. Грабли (важно учесть)

1. **Атомарность платёж↔провизия.** Если payment-webhook пришёл, а вызов в Remnawave упал — НЕ помечать payment как `paid`. Webhook повторит, попадём в шаг 3 (find by username), адоптируем существующую запись (если она успела создаться) или создадим заново.
2. **`expireAt` формат.** Только `YYYY-MM-DDTHH:MM:SSZ` (UTC, с `Z`). Все остальные форматы Remnawave молча принимает, но потом отдаёт обратно как `null` — подписка получается без срока.
3. **`uuid` vs `vlessUuid`.** Эти два разных. `uuid` — для управления (PATCH/DELETE), `vlessUuid` — для подключения. В БД храни оба.
4. **`activeInternalSquads`.** Если забыть передать — пользователь создаётся, но **не привязан ни к одному inbound**, подписка не работает. В ответе тоже проверяй: если `activeInternalSquads` пуст — вызови `POST /api/squads/add-users-to-squad` (см. как сделано в `app/services/remnawave_api.py:214` в Atlas).
5. **Логика find_by_username для recovery.** Если БД упала и потеряли panel_uuid, по telegram_id всегда можем восстановить через `GET /api/users/by-username/elma_<tg_id>` → достаём uuid обратно в БД. Это важная страховка.

---

## 11. ENV для Railway

После реализации добавь в Railway → Variables:

```env
# === Remnawave panel (общая с Atlas Secure) ===
REMNAWAVE_URL=https://rmnw.atlassecure.ru
REMNAWAVE_TOKEN=<тот же сервисный токен, что у Atlas, ИЛИ заведи отдельный для Elma>

# === MainSquad — куда кладём всех юзеров Elma ===
REMNAWAVE_MAIN_SQUAD_UUID=<скопируй из Atlas Railway Variables — там REMNAWAVE_MAIN_SQUAD_UUID>

# === Username prefix (изоляция от Atlas) ===
REMNAWAVE_USERNAME_PREFIX=elma_

# === Параметры подписки ===
SUBSCRIPTION_DAYS=30
TRIAL_DAYS=3
DEVICE_LIMIT=5
TRAFFIC_LIMIT_BYTES=0          # 0 = unlimited

# === Бот ===
BOT_TOKEN=<токен нового бота>
ADMIN_TELEGRAM_ID=<твой Telegram ID>

# === БД ===
DATABASE_URL=<привязка Railway PostgreSQL plugin>

# === Платежи (один из) ===
# Вариант A — Telegram Stars (проще всего, без webhook'ов):
PRICE_STARS=99

# Вариант B — Telegram Stars + RUB через провайдера:
# PAYMENT_PROVIDER_TOKEN=...
# PRICE_RUB_KOPECKS=29900
```

**Что обязательно отличить от Atlas:**
- `BOT_TOKEN` — новый бот, не переиспользовать.
- `REMNAWAVE_USERNAME_PREFIX=elma_` — чтобы записи не пересекались с `tg_<id>_premium` у Atlas.
- `DATABASE_URL` — отдельная БД (или хотя бы отдельная схема), иначе таблицы перепутаются.

**Что можно одинаковое:**
- `REMNAWAVE_URL`, `REMNAWAVE_TOKEN` (или сделай отдельный токен в панели — `Settings → API Keys → Create`), `REMNAWAVE_MAIN_SQUAD_UUID` — это всё одна и та же панель и один и тот же сервер.

---

## 12. Чек-лист готовности

- [ ] Реализован `app/services/remnawave.py` (4 функции из п. 8).
- [ ] Реализован `app/services/subscription_service.create_or_renew(...)` (алгоритм п. 6).
- [ ] Таблица `subscriptions` с правильными типами (TIMESTAMPTZ).
- [ ] Хендлеры триала / покупки / админ-выдачи вызывают `create_or_renew`.
- [ ] Scheduler-loop отзывает доступ (`delete_user`) у истёкших.
- [ ] При сбое в панели payment остаётся `pending`, webhook повторит.
- [ ] Тест на recovery: разово удалить запись в БД, повторно запустить flow — должна сработать ветка find_by_username и адоптировать панельную запись.
- [ ] В Railway прописаны все env из п. 11.

После прохождения чек-листа — бот готов выдавать и продлевать подписки через ту же панель, что и Atlas Secure, без конфликтов имён.
