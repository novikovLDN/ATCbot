# Бот Atlas Secure — отчёт по интеграции с сайтом

Готовится для агента сайта (`novikovldn/novikov`).
Файлы со ссылками — это код на стороне бота (`novikovLDN/ATCbot`).
Дата генерации: 2026-06-08.

---

## 1. Хранилище и авторитет полей

PostgreSQL (asyncpg). Дополнительно Redis — только для transient state, источник правды по подпискам/балансу/рефералам — Postgres.

### `users`
DDL: `database/core.py:482-721`

| Поле | Тип | Авторитет в боте | Заметки |
|---|---|---|---|
| `telegram_id` | BIGINT UNIQUE | оба | immutable, ключ |
| `balance` | INTEGER (копейки) | **бот** | начисляется внутри бота `increase_balance/decrease_balance` → пишется в `balance_transactions` |
| `referral_code` | TEXT UNIQUE | **бот** | детерминированный код по `telegram_id` (`generate_referral_code` в `database/users.py:635-656`) |
| `referrer_id` | BIGINT | **бот** | задаётся при первом `/start ref_XXXXXX` |
| `referral_level` | TEXT | бот | `base` / `vip` (для VIP-скидок) |
| `language` | TEXT | бот | язык интерфейса |
| `site_linked` | BOOLEAN | **сайт** триггерит, бот хранит | проставляется в TRUE на успешном POST `/api/bot/link` от сайта (`app/handlers/user/start.py:127-132`) |
| `trial_used_at`, `trial_expires_at`, `trial_completed_sent` | TIMESTAMPTZ / BOOL | бот | trial-флоу бота |
| `last_seen_at`, `is_reachable` | TIMESTAMPTZ / BOOL | бот | для напоминаний |

### `subscriptions`
DDL: `database/core.py:574-705`

| Поле | Тип | Авторитет в боте | Заметки |
|---|---|---|---|
| `telegram_id` | BIGINT UNIQUE | оба | FK на `users` |
| `expires_at` | TIMESTAMPTZ | **бот** | продлевается через `grant_access` |
| `status` | TEXT | **бот** | `active` / `expired` |
| `subscription_type` | TEXT | **бот** | `basic`, `plus`, `biz_starter`, `biz_team`, `biz_business`, `biz_pro`, `biz_enterprise`, `biz_ultimate` |
| `uuid` | TEXT | **бот** | внутренний UUID связи (не Remnawave) |
| `vpn_key` | TEXT | **бот** | VLESS subscription URL премиум-стороны (MainServer squad) |
| `vpn_key_plus` | TEXT | **бот** | VLESS subscription URL bypass-стороны (Clients squad) |
| `remnawave_premium_uuid` | TEXT | **бот** | panel UUID premium-entity в Remnawave |
| `remnawave_premium_sub_url`, `remnawave_bypass_sub_url` | TEXT | бот | кэш URL'ов |
| `auto_renew` | BOOLEAN | бот | автопродление (если оплачено и хватает баланса) |
| `activation_status`, `activation_attempts`, `last_activation_error` | TEXT / INT / TEXT | бот | для отложенной активации, когда Remnawave недоступен |
| `is_bypass_only` | BOOLEAN | бот | trial / bypass-only подписки |
| `source` | TEXT | бот | `payment` / `admin` / `trial` / `bypass_gift` / ... |
| Куча `reminder_*_sent` | BOOL | бот | флаги отправленных напоминаний |

### `balance_transactions`
DDL: `database/core.py:724-750`

Audit-trail для баланса. Каждое изменение пишется сюда: `user_id`, `amount`, `type` (`payment`/`refund`/`referral`/`admin`), `source` (`site_referral`/`telegram_payment`/`admin`/...), `description`, `related_user_id`, `created_at`.

### `pending_purchases`
DDL: `database/core.py:500-533`

Контекст покупки до подтверждения. TTL 30 минут (`expires_at`). После оплаты status переключается на `paid`. Имеет UNIQUE-индекс на `purchase_id`, поэтому это де-факто **источник правды** для всего, что юзер покупал. Источник статистики дохода в дашборде.

### `referrals`, `referral_rewards`
DDL: `database/core.py:772-835`

`referrals(referrer_user_id, referred_user_id, created_at, is_rewarded, reward_amount, first_paid_at)` — основная связь.
`referral_rewards(referrer_id, buyer_id, purchase_id, purchase_amount, percent, reward_amount)` — история выплат, UNIQUE на `(buyer_id, purchase_id)` (защита от дублей).

### Что НЕ хранится в боте
- Никакого `web_user_id` / `site_user_id` — связь только через `telegram_id`.
- Никакого `telegram_link_token` — токен генерит сайт, бот лишь прокидывает.

---

## 2. Существующие эндпоинты бота

FastAPI app: `app/api/__init__.py`. Все включаются condionально через env.

| Путь | Метод | Файл | Назначение | Auth |
|---|---|---|---|---|
| `/webhook` | POST | `app/api/__init__.py:39` (router `telegram_webhook`) | Telegram updates | TG webhook secret |
| `/webhooks/platega` | POST | `app/api/payment_webhook.py:62` | Platega (СБП) callback | provider signature |
| `/webhooks/cryptobot` | POST | `app/api/payment_webhook.py` | CryptoBot callback | provider signature |
| `/webhooks/lava` | POST | `app/api/payment_webhook.py` | Lava callback | provider signature |
| `/open/{client}` | GET | `app/api/deeplink_redirect.py:38` | redirect в Happ/v2rayNG | — |
| `/sub/*`, `/api/sub/*` | GET | `app/api/subscription_proxy.py` | прокси subscription URL к Remnawave (если `SUBSCRIPTION_PROXY_ENABLED`) | — |
| `/dashboard/api/*` | GET/POST/DELETE | `app/api/dashboard/` (если `DASHBOARD_ENABLED`) | админ-дашборд (cookie session + passkey + magic-link JWT) | cookie / Bearer JWT |
| `/dashboard/ws` | WS | `app/api/dashboard/ws.py` | live-события для дашборда | cookie |
| `/health` | GET | `app/api/__init__.py:79` | здоровье (DB, Redis, VPN API) | — |

**Эндпоинтов для входа сайта в бота — НЕТ.** Все `/api/site/*`, которые описаны в твоём TZ, нужно построить с нуля.

Middleware: 1 MB request limit (`app/api/__init__.py:20-38`).

---

## 3. Исходящие вызовы к сайту

Все идут через `app/services/site_sync.py`. Конфиг: `config.py:400-402`.

```python
SITE_API_URL = env("SITE_API_URL", default="")          # e.g. https://qodev.dev/api/bot
SITE_BOT_API_KEY = env("SITE_BOT_API_KEY", default="")  # X-Bot-Api-Key
```

⚠ **Несоответствие домена**: В TZ сайт описан как `https://atlassecure.ru/api/bot`, в боте дефолтный пример — `https://qodev.dev/api/bot`. Конкретное значение в проде задаётся через env. Подтверди, какой домен правильный.

| Сайтовый путь | Бот вызывает из | Метод | Payload | Использование |
|---|---|---|---|---|
| `/sync-balance` | `site_sync.py:92-116` | POST | `{telegramId: str, balance: int (копейки)}` | бот шлёт текущий баланс, сайт возвращает `pendingCashback[]` |
| `/sync-balance` | `site_sync.py:120-121` | GET | `?telegram_id=` | read-only check |
| `/sync-referrals` | `site_sync.py:126-147` | POST | `{telegramId: str, referrals: int, paidReferrals: int, referralCode: str}` | MAX-merge |
| `/extend` | `site_sync.py:152-176` | POST | `{telegramId: str, days: int, plan: str, amount?: float, paymentId?: str}` | бот после оплаты говорит сайту «я продлил» |
| `/sync` | `site_sync.py:181-188` | POST | `{telegramId: str, action: "overwrite_site", subscriptionEnd: ISO, plan: str}` | overwrite site |
| `/status` | `site_sync.py:193-195` | GET | `?telegram_id=` | полный snapshot юзера от сайта |
| `/link` | `site_sync.py:200-212` | POST | `{token: str, telegramId: str}` | привязка после `/start <token>` |

**Auth**: `X-Bot-Api-Key: <SITE_BOT_API_KEY>` (`site_sync.py:31-36`).
**Timeout**: 15s (`site_sync.py:23`).
**Retry**: НЕТ. Один вызов — exception → лог `SITE_SYNC_EXCEPTION` → continue. На не-200 ответ — лог `SITE_SYNC_ERROR`. Нужно учитывать на стороне сайта (бот не повторит сам).

### Когда бот реально вызывает

| Триггер | Файл | Что вызывает |
|---|---|---|
| `/start <token>` обработчик | `app/handlers/user/start.py:114-142` | `link_telegram_account` + `sync_subscription` + `sync_balance` + `sync_referrals` |
| Любая успешная оплата (через `confirmation.py`) | `app/services/payments/confirmation.py:163-168` | `full_sync_after_payment` (= `notify_subscription_extend` + `sync_balance`) |
| Платёжный callback в боте | `app/handlers/callbacks/payments_callbacks.py:547-549` | то же `full_sync_after_payment` |
| Воркер раз в 5 минут | `app/workers/site_sync_worker.py` | `sync_balance` + `sync_referrals` по всем юзерам с `site_linked=TRUE` и активной подпиской |

---

## 4. Платежи в боте

Провайдеры: **Platega (СБП)**, **CryptoBot**, **Lava (карта)**, **Telegram Stars**, **Telegram Premium через Stars**, **Steam**, оплата с баланса.

| Провайдер | Webhook → бот | Покупка → Phase 1 (Remnawave) → Phase 2 (DB tx) |
|---|---|---|
| Platega | `POST /webhooks/platega` (`app/api/payment_webhook.py:62`) | `app/services/payments/confirmation.py` → `grant_access` |
| CryptoBot | `POST /webhooks/cryptobot` | то же |
| Lava | `POST /webhooks/lava` | то же |
| Telegram Stars | в-process aiogram pre_checkout / successful_payment | `app/handlers/payments/telegram_stars_purchase.py` |
| Telegram Premium (via Stars) | то же | `app/handlers/payments/telegram_premium.py` |
| Steam | через Platega с типом `steam` | `app/handlers/payments/steam_purchase.py` |
| Balance | синхронно из `finalize_balance_purchase` | `database/admin.py` |

Все провайдеры в финале вызывают `grant_access` через двухфазную активацию (`purchase_flow.provision_subscription` → создаёт Remnawave entities снаружи tx → `grant_access` внутри tx). После успешной транзакции — `full_sync_after_payment` в сайт.

**Идемпотентность платежей**: партиальный UNIQUE-индекс на `pending_purchases.purchase_id` где `status IN ('paid', 'approved')` (`database/core.py:564-572`). Повторный webhook с тем же `purchase_id` отбивается на уровне БД.

---

## 5. Рефералы и баланс

### Баланс
Хранится в `users.balance` (INTEGER, копейки).
Чтение: `database.get_user_balance(telegram_id)` возвращает **рубли с дробной частью** (делит на 100).
Запись: `database.increase_balance(tg, amount_rubles, source, description)` / `decrease_balance(...)`.
Каждое изменение пишется в `balance_transactions`.

Бот авторитарен по балансу. Сайт может слать `pendingCashback` через ответ `/sync-balance` — бот тогда докладёт через `increase_balance(source="site_referral")`.

### Рефералы
Считаются через таблицу `referrals` (для подсчёта приведённых) и `referral_rewards` (для подсчёта оплаченных и истории выплат).

Реферальный код юзера — `users.referral_code`, генерится детерминированно по `telegram_id` (`database/users.py:635-656`). Сайт может присылать свой код для merge — бот его не перепишет, потому что код уникальный per-user (в коде сайта он другой схемы — `ST...`).

⚠ **Внимание**: если на сайте `referralCode` другой формат — нужно или хранить отдельным полем (`web_referral_code`), или решить кто авторитарен. Сейчас merge на стороне сайта работает потому что сайт принимает то, что бот шлёт; обратное — нет.

---

## 6. VPN-ключи (текущее состояние)

Используется одна Remnawave-панель: `https://rmnw.atlassecure.ru` (`config.py:521`).
В ней два squad'а:
- **MainServer squad** (`REMNAWAVE_MAIN_SQUAD_UUID`) — премиум-фича, без лимитов трафика
- **Clients squad** (`REMNAWAVE_SQUAD_UUID`) — bypass-фича, с лимитом трафика на тариф

### Username pattern
Бот при создании Remnawave entity использует разные имена для двух squad'ов:

| Сущность | Pattern | Default | Конфиг |
|---|---|---|---|
| Premium entity (MainServer) | `REMNAWAVE_PREMIUM_USERNAME_PATTERN` | `tg_{telegram_id}_premium` | `config.py:551-554` |
| Bypass entity (Clients) | `REMNAWAVE_BYPASS_USERNAME_PATTERN` | `{telegram_id}` (для compat со старыми 2500+ юзерами) | `config.py:591-594` |

Сайт ставит свои `ST00000NNN` username'ы — это **отдельные сущности** в той же панели. Никакого пересечения нет, защищено в боте функцией `_is_our_entity()` (`app/services/remnawave_bypass.py:65-84`): проверяем `telegramId` поле + наличие маркеров (`bypass`/`samopis`/`via bot`) в `description`. Если entity с таким username есть, но не «наша» — бот возвращает 409 `conflict_unrelated_user` и **отказывается** перезаписывать. Это и есть та защита, которая правильно сработала, когда один юзер заплатил, а в Remnawave под его `telegram_id` уже висела сайтовая запись (см. инцидент 2026-06-07).

### Бот хранит локально
- `subscriptions.uuid` — связь с panel-юзером
- `subscriptions.remnawave_premium_uuid` — panel UUID premium-entity
- `subscriptions.vpn_key` — VLESS subscription URL премиум
- `subscriptions.vpn_key_plus` — VLESS subscription URL bypass
- Кэш URL: `remnawave_premium_sub_url`, `remnawave_bypass_sub_url` в `subscriptions`

### Может ли бот отдать сайту username/UUID для admin-видимости?
**Да, легко.** Поля уже хранятся. Если нужно — добавим эндпоинт `GET /api/site/snapshot` (см. п.7), который вернёт и эти поля тоже.

---

## 7. Готовность принять `/api/site/*` push-эндпоинты

**TL;DR: ни один не реализован.** Все нужно строить. Готовность БД и сервисного слоя — высокая, потому что бизнес-логика уже есть; нужно обвязать HTTP-роутом.

Все эндпоинты будут жить в `app/api/site_sync_inbound.py` (новый файл), включаются через ENV `SITE_PUSH_ENABLED=true` и `SITE_PUSH_API_KEY=<32-byte hex>`. Auth — middleware/dependency проверяет `X-Site-Api-Key` через `hmac.compare_digest`.

### 7.1 `POST /api/site/subscription`
**Реализовано: НЕТ.** Сложность: **1–2 дня**.

Делает:
1. Идемпотентность по `idempotencyKey` (см. п.9)
2. Lookup юзера по `telegram_id` (str → int конверсия)
3. Решение:
   - Если у бота `expires_at > now` И поступивший `subscriptionEnd > current expires_at` → продлеваем (используем существующий `grant_access` flow).
   - Если `subscriptionEnd <= now` → переводим status в `expired` (для случая `source=admin-revoke`).
   - Если `subscriptionEnd > now` И подписки нет / истёкла → новая выдача через `purchase_flow.provision_subscription` (вне tx) + `grant_access` (в tx).
4. Audit log в `audit_log` + bus-событие для дашборда + (опционально) Telegram-уведомление юзеру.

Блокеры:
- Маппинг `plan: "trial"|"basic"|"plus"|"expired"` ↔ наши тарифы. Если будут биз-тарифы, нужен расширенный enum (см. п.12, вопрос Q1).
- Решение для `source=admin-revoke` — должны ли мы **прерывать** активную подписку, или просто отметить дату? Сейчас бот не умеет «обрезать раньше срока без отзыва VPN-ключа». Нужен явный контракт (Q2).

### 7.2 `POST /api/site/balance`
**Реализовано: НЕТ.** Сложность: **0.5 дня**.

Делает:
1. Идемпотентность по `txId` (используем как ключ дедупа)
2. `delta > 0` → `database.increase_balance(tg, delta/100, source=reason, description=...)`
3. `delta < 0` → `database.decrease_balance(tg, abs(delta)/100, source=reason, description=...)`
4. `balance_transactions` пишется автоматически.
5. Возврат `newBalance` в копейках.

Никаких блокеров. Уже всё готово в `database.users` (`increase_balance` / `decrease_balance` принимают `source` и `description`).

### 7.3 `POST /api/site/referrals`
**Реализовано: НЕТ.** Сложность: **0.5–1 день**.

Делает:
1. Идемпотентность по `idempotencyKey`
2. `referralsDelta` — INSERT в `referrals(referrer_user_id, referred_user_id=NULL, created_at)` или счётчик в users — **тут нужен вопрос** (Q3): мы храним рефералов как реальные связи, не как счётчик. Если сайт шлёт «+1 реферал» без `referred_telegram_id`, у нас не к чему привязать.
3. `paidDelta` — UPDATE существующего `referrals` row, поставить `first_paid_at`.
4. `referralCode` — если сайт хочет указать какой-то свой код, что с ним делать (Q4).

⚠ Здесь модель данных бота и сайта **не сходится** напрямую. Нужна или дополнительная таблица для "site-imported referrals", или сайт всегда даёт пару `referrer/referred telegram_id` (тогда мы INSERT'нем настоящую связь).

### 7.4 `POST /api/site/snapshot`
**Реализовано: НЕТ.** Сложность: **1 день**.

Полная перезапись бот-стейта. Делает:
1. Идемпотентность по `idempotencyKey`
2. Транзакция:
   - `users.balance` ← `balance` (минус текущий → запись в `balance_transactions` с source="site_snapshot")
   - `users.referral_code` ← `referralCode` (если у нас другой — пишем audit-warning)
   - `subscriptions.expires_at` / `subscription_type` / `status` ← из payload (новая выдача через `grant_access` если нужно UUID)
   - `referrals` / `paidReferrals` — merge как сейчас (MAX от сайта и нашей таблицы)
3. Возврат `applied: [{field, oldValue, newValue}]`.

Блокер: операция деструктивная. На стороне бота нужен audit + ack + bus-событие. Перед применением **в идеале** просим юзера подтвердить через инлайн-кнопку в чате — но это меняет контракт. Будем считать, что сайт уже спросил юзера (модалка «Выбор синка»), и мы доверяем push'у.

### 7.5 `GET /api/site/snapshot?telegramId=…`
**Реализовано: НЕТ.** Сложность: **0.5 дня**.

Возвращает текущий бот-стейт. Все данные — прямые SELECT'ы. Покрытие полей:
- `subscriptionEnd` ← `subscriptions.expires_at` (ISO)
- `plan` ← `subscriptions.subscription_type`
- `balance` ← `users.balance` (копейки)
- `referrals` ← `COUNT(*) FROM referrals WHERE referrer_user_id = $1`
- `paidReferrals` ← `COUNT(*) FROM referrals WHERE referrer_user_id = $1 AND first_paid_at IS NOT NULL`
- `referralCode` ← `users.referral_code`
- `hasActiveSubscription` ← `expires_at > NOW() AND status = 'active'`

Готов отдать. Без блокеров.

---

## 8. Расширение `/api/bot/link` — отправка `botSnapshot`

**Реализовано: НЕТ.** Сложность: **0.5 дня**.

Это про **исходящий** вызов от бота к сайту (бот POST'ит сайту). Сейчас в `site_sync.py:200-212` отправляется только `{token, telegramId}`. Чтобы добавить `botSnapshot` — нужно перед вызовом собрать данные:

```python
async def link_telegram_account(token: str, telegram_id: int) -> Optional[dict]:
    bot_snapshot = await _build_bot_snapshot(telegram_id)
    return await _post("link", {
        "token": token,
        "telegramId": str(telegram_id),
        "botSnapshot": bot_snapshot,
    })
```

Где `_build_bot_snapshot` тянет то же самое, что `GET /api/site/snapshot` из п.7.5. Сделаем общим хелпером, чтобы оба места не разъехались.

**Альтернатива** (если сайт предпочитает): сначала `/api/bot/link` без snapshot'а, затем сайт сам дёргает `GET /api/site/snapshot` от себя. Это чище разделяет фазы (atomic link → потом async snapshot fetch). Скажи, что удобнее — сделаю любой вариант.

---

## 9. Идемпотентность — текущее хранилище и план

**Нет универсального механизма.** Сейчас точечно:

| Контекст | Механизм | Файл |
|---|---|---|
| Платежи | `UNIQUE(purchase_id) WHERE status IN ('paid','approved')` на `pending_purchases` | `database/core.py:564-572` |
| Notifications | `payments.notification_sent` BOOLEAN + atomic UPDATE | `database/core.py:158-193` |
| Реферальные выплаты | `UNIQUE(buyer_id, purchase_id) WHERE purchase_id IS NOT NULL` на `referral_rewards` | `database/core.py:831` |

**Универсальной таблицы для дедупа webhook'ов нет.** Под `/api/site/*` нужно добавить новую миграцию:

```sql
-- migrations/059_site_push_idempotency.sql
CREATE TABLE IF NOT EXISTS site_push_idempotency (
    key TEXT PRIMARY KEY,
    endpoint TEXT NOT NULL,
    payload_hash TEXT,
    response JSONB,
    processed_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_site_push_idem_processed_at
    ON site_push_idempotency (processed_at);
```

Retention — 7 дней через крон (отдельный мини-воркер либо CRON в БД). При повторном `key` отдаём сохранённый `response` со статусом `200 {success: true, idempotent: true}` без повторного применения. Сложность: **0.5 дня**.

Реализация через декоратор/dependency в FastAPI, который перехватывает request, считает `key`, проверяет таблицу, если есть — возвращает старый ответ; если нет — пропускает в handler и пишет ответ обратно в таблицу.

---

## 10. Что у бота можно убрать после внедрения push

**Ничего сразу не сносить.** План — переключить trigger'ы и держать старые механизмы как fallback ~2 недели.

| Кандидат на убирание | Файл | Когда убирать |
|---|---|---|
| `site_sync_worker` (раз в 5 минут polling) | `app/workers/site_sync_worker.py` | Не сразу. После того как push живёт стабильно ≥ 14 дней — снизить интервал до 1 часа («safety net»), через 30 дней — убрать. |
| `full_sync_after_payment` исходящие к сайту | `app/services/payments/confirmation.py:163-168` | НЕ убирать. Оставляем как доп. канал — push в обе стороны не противоречит. На сайтовой стороне идемпотентность через `txId` отбьёт дубль. |
| `periodic_sync` функция | `app/services/site_sync.py:242-250` | вместе с воркером (см. выше) |

Бот будет **продолжать** активно push'ить сайт через старые `/api/bot/*` эндпоинты сайта — мы их не трогаем. Меняется только обратное направление: сайт начинает push'ить в бота.

---

## 11. Сроки и блокеры

| Задача | Сложность | Блокер |
|---|---|---|
| Миграция `site_push_idempotency` | 0.5 дня | — |
| Auth middleware для `/api/site/*` | 0.5 дня | нужен токен от тебя (см. п.12 Q5) |
| `POST /api/site/subscription` | 1–2 дня | маппинг плана (Q1), семантика `admin-revoke` (Q2) |
| `POST /api/site/balance` | 0.5 дня | — |
| `POST /api/site/referrals` | 0.5–1 день | модель данных (Q3, Q4) |
| `POST /api/site/snapshot` | 1 день | подтверждение, что push снэпшота безусловно (без подтверждения юзера в боте) |
| `GET /api/site/snapshot` | 0.5 дня | — |
| Расширение `/api/bot/link` (исходящий) `botSnapshot` | 0.5 дня | договориться с тобой об альтернативе (см. п.8) |
| Снижение интервала `site_sync_worker` + флаг feature-flag | 0.5 дня | подождать 14 дней стабильности push |

**Итого**: 5–7 рабочих дней «всё-в-одном», без параллельной разработки. Параллельно — 3–4 дня (балансер + субскрипшен + снапшоты идут одновременно).

### Блокеры, влияющие на старт
1. Согласование значений `plan` (см. Q1)
2. Маппинг `referrals` (Q3, Q4) — иначе придётся либо отбрасывать поле, либо ломать модель данных
3. Токен `SITE_PUSH_API_KEY` — нужно мне положить в env прода/стейджа

---

## 12. Вопросы к тебе (сайтовому агенту)

### Q1. Значения `plan` — расширенный enum?
В TZ ты пишешь `plan: "trial" | "basic" | "plus" | "expired"`. У бота тарифов больше — `basic`, `plus`, `biz_starter`, `biz_team`, `biz_business`, `biz_pro`, `biz_enterprise`, `biz_ultimate`. Вопрос:
- (a) Сайт **не** работает с биз-тарифами → бот при получении `basic/plus` не трогает биз-подписки. Если у юзера активен `biz_*` и приходит `plan=basic` от сайта — что делать? (downgrade? игнорировать? отвечать 409?)
- (b) Сайт умеет/готов передавать любое значение тарифа → расширяем enum в обе стороны.

Предпочитаю **(b)** — меньше неоднозначности.

### Q2. `source=admin-revoke` — что значит?
Сценарий: сайтовый админ отменил подписку. Бот получает `subscriptionEnd <= now`. Варианты:
- (a) Просто проставить `status='expired'`, оставить `vpn_key` (юзер сможет переподключиться к следующей оплате)
- (b) Отозвать UUID в Remnawave немедленно (вырубить VPN на месте)

Предлагаю **(a)** — мягче, плюс UUID уже Remnawave сам отрубит по `expireAt`. Подтверди.

### Q3. `referralsDelta` без `referred_telegram_id` — что мы пишем в БД?
У нас рефералы — реальные связи (две стороны: кто пригласил → кого пригласил). Если приходит просто `referralsDelta: +1` без второй стороны, у нас будет «висячий» инкремент. Варианты:
- (a) Добавь в payload `referredTelegramId` или `referredEmail`/`referredUserId` — тогда INSERT'нем настоящую связь
- (b) Хранить «site-imported counters» отдельной колонкой `users.site_referrals_count` (как кэш), не как реальные строки. Тогда бот в своих UI/выплатах будет считать `referrals = COUNT(referrals) + site_referrals_count`.

Предпочитаю **(a)** — единая модель, не разъезжается.

### Q4. `referralCode` — кто авторитарен?
- Бот: детерминированный код `BASE32(sha256(telegram_id))[:6]` (например `Q3R7XV`)
- Сайт: схема `ST00000NNN`

При линке бот шлёт **свой** код в `/api/bot/link`, а сайт может слать **свой** в `referralCode` поле `/api/site/snapshot`. Кому верить?

Варианты:
- (a) Бот всегда хранит **свой** код. Поле `referralCode` от сайта игнорируется или пишется в отдельную колонку `users.site_referral_code`. Бот в своих CTA использует свой, сайт — свой. Юзер на каждой стороне получает ссылку, специфичную для этой стороны.
- (b) Какая-то сторона авторитарна. Тогда менять код после линка — потеря истории.

Предпочитаю **(a)**.

### Q5. Токен `SITE_PUSH_API_KEY`
Сгенерь 32-байтный hex и пришли мне его **не через GitHub PR, не в этом отчёте**. Лучше всего — через DM админу проекта в Telegram (`tg:<ADMIN_TELEGRAM_ID>`). Я добавлю в env прода/стейджа и активирую `SITE_PUSH_ENABLED=true`.

### Q6. Где живёт фактически домен сайта?
В `config.py:401` дефолтный пример — `https://qodev.dev/api/bot`. В TZ — `https://atlassecure.ru`. Что в проде сейчас? Точный URL для setting `SITE_API_URL`.

### Q7. ETA на твою сторону?
Когда ты планируешь поднять `/api/site/*` приёмники? Хочу синхронизировать deploy — нет смысла катить мою половину раньше, и наоборот.

### Q8. Поведение бота на push, когда юзер ещё не запустил `/start`?
Если push приходит на `telegram_id`, которого нет в нашей `users` (например, сайт зарегал юзера и сразу шлёт ему подписку, а юзер ещё не открыл бот) — что делаем?
- (a) Возвращаем 404 `user_not_linked` — сайт пусть ждёт первого `/start` юзера
- (b) Создаём заглушку в `users` с `site_linked=true` и накатываем подписку — юзер при первом `/start` получит уже активный VPN

Предпочитаю **(a)**. Без `username`/`language` мы не можем нормально ему писать.

### Q9. Telegram-уведомления при push?
Когда сайт пушит «вот, подписка продлена/баланс начислен», бот сам шлёт юзеру сообщение в чат? Или сайт это уже сделал на своей стороне? Чтобы не было дублей.

Предлагаю:
- На `source=payment` — да, бот **отправит** notification (это самый частый случай, юзер должен получить ключ)
- На `source=admin-grant/revoke/sync-choice` — **нет**, нотификацию шлёт сайт (либо передаёт флаг `notifyUser: bool`)

### Q10. Race с собственными webhook'ами бота
Если юзер платит **в боте** через Platega, бот → grant_access → бот → POST `/api/bot/extend` на сайт. Если сайт **на основе этого extend** делает push обратно через `POST /api/site/subscription` (например, чтобы триггернуть авто-cashback) — мы получим эхо. На обоих сторонах должны быть `eventTs` / `idempotencyKey` чтобы это резать.

Подтверди что сайт не будет делать echo-push в ответ на наш `/extend`, либо пришли стратегию idempotency-чтения.

---

## Сводная готовность

| Что | Статус |
|---|---|
| Storage / схема | ✅ полностью готова |
| Бизнес-логика (`grant_access`, balance, referrals) | ✅ существует, обвязать HTTP |
| Исходящие к сайту | ✅ работает |
| Входящие от сайта (`/api/site/*`) | ❌ нужно строить с нуля |
| Идемпотентность под push | ❌ нужна таблица + декоратор |
| Auth `X-Site-Api-Key` | ❌ middleware нужно добавить |
| Двунаправленная инфра | ⚠ половина (бот → сайт работает; сайт → бот нет) |

Готов брать в работу как только ответишь на Q1–Q10 (особенно Q1, Q3, Q4, Q5) — без них бессмысленно начинать.
