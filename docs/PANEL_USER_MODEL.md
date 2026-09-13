# Как бот заводит и называет пользователей в панели (Remnawave 3.x)

Документ для ТЗ: какой username/идентификатор и какие поля бот ставит в панели
при создании пользователя, и как устроена выдача подписок «премиум» и «обход
(bypass)». Составлено по коду (`app/services/remnawave_api.py`,
`remnawave_premium.py`, `remnawave_bypass.py`, `purchase_flow.py`).

---

## 1. Идентификатор пользователя в панели

Бот на **каждого** своего пользователя заводит в панели **две отдельные
сущности** (user): «премиум» и «обход». Идентификатор пользователя — его
**Telegram ID**, передаётся двумя способами.

| Поле в панели | Премиум-сущность | Обход-сущность (bypass) |
|---|---|---|
| `username` | `tg_{telegram_id}_premium` (напр. `tg_738398394_premium`) | `{telegram_id}` — числовой ID (напр. `738398394`) |
| `telegramId` | `{telegram_id}` (число) | `{telegram_id}` (число) |
| **tags** | **не ставит** | **не ставит** |

- **Теги бот не использует** — поля `tags` при создании нет.
- Шаблоны username настраиваются через ENV
  (`REMNAWAVE_PREMIUM_USERNAME_PATTERN`, `REMNAWAVE_BYPASS_USERNAME_PATTERN`),
  но дефолты именно такие. Username обрезается до 32 символов (лимит панели).
- Для сайта достаточно: **идентификатор = Telegram ID**. По нему однозначно
  находятся обе сущности (`{id}` и `tg_{id}_premium`), плюс панельное поле
  `telegramId` = тот же ID.

---

## 2. Что пишется при создании (`POST /api/users`)

Общий JSON-запрос:

```json
{
  "username": "<см. таблицу п.1>",
  "shortUuid": "<12 hex>",
  "trafficLimitBytes": 0,
  "trafficLimitStrategy": "NO_RESET",
  "status": "ACTIVE",
  "expireAt": "2026-10-13T12:00:00Z",
  "hwidDeviceLimit": 5,
  "deviceLimit": 5,
  "telegramId": 738398394,
  "description": "Premium via bot (basic)",
  "activeInternalSquads": ["<squad uuid>"],
  "externalSquadUuid": "<опц., только премиум>"
}
```

Различия по сущностям:

| Параметр | Премиум | Обход (bypass) |
|---|---|---|
| `trafficLimitBytes` | `0` = **безлимит** | конкретный лимит в байтах (пакет ГБ: 1/10/75 ГБ и т.п.; триал — 500 МБ) |
| `expireAt` | дата окончания подписки | **«бесконечность»** `2099-12-31T23:59:59Z` (обход лимитируется трафиком, не временем) |
| `deviceLimit` / `hwidDeviceLimit` | `REMNAWAVE_PREMIUM_DEVICE_LIMIT` (деф. 5) | `REMNAWAVE_BYPASS_DEVICE_LIMIT` (деф. 5) |
| `activeInternalSquads` | `REMNAWAVE_MAIN_SQUAD_UUID` (основные сервера) | `REMNAWAVE_CLIENTS_SQUAD_UUID` / `REMNAWAVE_SQUAD_UUID` (серверы обхода) |
| `externalSquadUuid` | `REMNAWAVE_PREMIUM_EXTERNAL_SQUAD_UUID` (если задан) — переключает шаблон подписки на «Unlimited» | не ставится |
| `description` | `Premium via bot ({tariff})` | `Bypass via bot ({tariff})` |
| `trafficLimitStrategy` | `NO_RESET` (трафик не сбрасывается) | `NO_RESET` |
| `status` | `ACTIVE` | `ACTIVE` |

Ответ панели содержит числовой `id`, `vlessUuid` и `subscriptionUrl`. Бот
сохраняет у себя:

- `id` → `remnawave_premium_id` (премиум) / `remnawave_id` (обход);
- `subscriptionUrl` → кеш `remnawave_premium_sub_url` / `remnawave_bypass_sub_url`.

---

## 3. Логика выдачи (create vs renew)

**Создание идемпотентно (preflight):** перед `POST` бот ищет пользователя по
`username`; если он уже есть и принадлежит нам (совпал `telegramId` либо наш
`username`/маркер в `description`) — **адоптит** существующую сущность, дубль не
создаёт. Гонка с ответом `409` тоже обрабатывается (повторный поиск + адопт).

- **Новая / истёкшая подписка** → создаём (или адоптим) обе сущности с
  параметрами из п.2.
- **Продление (renew)** активной подписки:
  - **Премиум:** сущность НЕ пересоздаётся, **UUID/ключ стабильны** — только
    `PATCH expireAt` = новая дата окончания.
  - **Обход:** лимит трафика **накапливается** — читаем текущий
    `trafficLimitBytes` и `PATCH` на сумму (никогда не сбрасываем, `NO_RESET`).
    Докупка ГБ = прибавка к остатку.
- Премиум и обход **независимы**: обход живёт с «бесконечным» `expireAt` и
  работает, даже когда премиум-подписка истекла (пока есть остаток ГБ).

---

## 4. Что отдаётся наружу (для сайта)

- Пользователь идентифицируется **Telegram ID**.
- Клиенту выдаётся **`subscriptionUrl`** с панели (публичный хост
  `sub.atlassecure.ru`), отдельно для премиум и для обхода — это «ключ
  подписки» (plain-ссылка). Для Happ/Incy она дополнительно оборачивается в
  `happ://crypt4/…` / `incy://crypt1/…`.

---

## Готовая формулировка для ТЗ

> Бот создаёт в панели две сущности на пользователя: `username =
> tg_{telegram_id}_premium` (премиум) и `username = {telegram_id}` (обход), и
> проставляет панельное поле `telegramId` = Telegram ID. Теги не используются.
> Идентификатор пользователя в панели = Telegram ID — по нему находятся обе
> сущности и их `subscriptionUrl`.
