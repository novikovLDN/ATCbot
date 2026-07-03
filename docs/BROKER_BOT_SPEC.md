# ТЗ: Broker-бот для перепродажи GB обхода партнёру

**Статус:** draft-1 · **Целевая аудитория:** разработчик или агент, реализующий Broker-сервис с нуля  
**Проект-эмитент подписок:** Atlas Secure (существующая инфраструктура + Remnawave-панель)  
**Партнёр:** одно юр./физ. лицо, у которого свой Telegram-бот перепродаёт нашу услугу.

---

## 1. Что это и зачем

Партнёр хочет продавать в своём Telegram-боте **пакеты трафика для обхода блокировок**,
использующие нашу инфраструктуру (нашу Remnawave-панель и наши сервера-выходы).
Напрямую пускать партнёрский бот к нашей панели небезопасно (утечка API-токена, риск
влияния на наших юзеров). Поэтому вводим прокси-звено — **Broker-бот**.

```
┌─────────────────────┐          ┌───────────────────┐          ┌────────────────────┐
│ Партнёрский проект  │ ── HTTP ▶│  Broker-бот       │ ── HTTP ▶│ Наша Remnawave-    │
│ (его Telegram-бот   │          │  (новый сервис)   │          │ панель             │
│  + backend)         │◀──JSON ──│                   │◀── JSON ─│                    │
└─────────────────────┘          └───────┬───────────┘          └────────────────────┘
                                         │ Telegram Bot API
                                         ▼
                                 ┌───────────────────┐
                                 │ Админ (мы) в чате │
                                 │  с Broker-ботом:  │
                                 │   /topup /balance │
                                 └───────────────────┘
```

**Свойства сервиса:**
- Broker хранит единый **GB-баланс партнёра**.
- Админ пополняет баланс командой в чате Broker-бота.
- Партнёрский бэкенд шлёт `POST` в Broker и получает готовую подписочную ссылку
  за **< 500 мс типично, ≤ 2 сек worst-case**.
- Namespace юзеров в панели: `partner_<tg_id>` — не пересекается с нашими `tg_<id>_*`.

---

## 2. Границы ответственности

| Что | Broker | Партнёрский бот |
|---|---|---|
| Приём платежей от конечных юзеров | ❌ | ✅ (полностью на его стороне) |
| Ведение GB-баланса партнёра | ✅ | ❌ |
| Создание/добор энтити в Remnawave | ✅ | ❌ |
| Хранение api-ключа партнёра (hashed) | ✅ | плейн-ключ у себя в env |
| Возврат `subscription_url` конечному юзеру | получает URL от Broker → отдаёт партнёру | получает от Broker → отдаёт юзеру |
| Мониторинг «трафик кончается у юзера» | ❌ | ✅ (свой саппорт) |
| Продление баланса партнёра | админ через `/topup` | партнёр просит нас |

---

## 3. Технологический стек Broker-бота

**Обязательно:**
- Python 3.11+
- `aiogram` 3.x (бот)
- `FastAPI` + `uvicorn` (HTTP-API)
- `asyncpg` (PostgreSQL-драйвер)
- `httpx` (клиент к Remnawave-API)
- `argon2-cffi` (hash api-key)
- `structlog` или `python-json-logger` (structured logs)

**Опционально:**
- `slowapi` (rate-limit)
- `prometheus-client` (метрики, если понадобятся)

**База:** отдельная PostgreSQL-БД. Не смешивать с Atlas-БД, чтоб при инциденте
можно было изолированно чинить.

**Deploy:** отдельный Docker-контейнер, отдельный `docker-compose` service либо
отдельный systemd-unit. Отдельный Telegram-бот (свой BOT_TOKEN).

---

## 4. Схема БД (Broker)

Одна миграция `001_init.sql`:

```sql
CREATE TABLE partners (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    telegram_id     BIGINT UNIQUE NOT NULL,  -- Telegram id владельца партнёра
                                             -- (кому мы кидаем алерты об остатке)
    api_key_hash    TEXT NOT NULL,           -- argon2 hash от plain-ключа
    balance_gb      NUMERIC(14, 3) NOT NULL DEFAULT 0,  -- allow fractional GB
    last_topup_gb   NUMERIC(14, 3),          -- для расчёта порога 20%
    low_balance_alert_sent BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    disabled_at     TIMESTAMPTZ                -- soft-delete
);
CREATE INDEX idx_partners_telegram_id ON partners(telegram_id);

CREATE TABLE partner_users (
    id                  SERIAL PRIMARY KEY,
    partner_id          INTEGER NOT NULL REFERENCES partners(id) ON DELETE RESTRICT,
    external_user_id    BIGINT NOT NULL,       -- Telegram id конечного юзера
                                               -- в проекте партнёра
    remnawave_uuid      TEXT NOT NULL,         -- id энтити в панели
    remnawave_username  TEXT NOT NULL,         -- 'partner_<external_user_id>'
    subscription_url    TEXT NOT NULL,         -- то, что мы отдаём партнёру
    total_gb_allocated  NUMERIC(14, 3) NOT NULL,  -- накопительная сумма
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_topup_at       TIMESTAMPTZ,
    UNIQUE (partner_id, external_user_id)
);
CREATE INDEX idx_partner_users_uuid ON partner_users(remnawave_uuid);

CREATE TABLE partner_transactions (
    id           BIGSERIAL PRIMARY KEY,
    partner_id   INTEGER NOT NULL REFERENCES partners(id) ON DELETE RESTRICT,
    kind         TEXT NOT NULL CHECK (kind IN ('topup', 'debit', 'refund')),
    gb_delta     NUMERIC(14, 3) NOT NULL,   -- +N для topup/refund, -N для debit
    balance_after NUMERIC(14, 3) NOT NULL,
    external_user_id BIGINT,                -- заполняется для debit/refund
    remnawave_uuid   TEXT,
    ref_note     TEXT,                      -- админ-комментарий при topup, или reason при refund
    correlation_id TEXT,                    -- сквозной id для трассировки request → tx → alert
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_ptx_partner_created ON partner_transactions(partner_id, created_at DESC);

CREATE TABLE api_requests_log (
    id           BIGSERIAL PRIMARY KEY,
    partner_id   INTEGER,
    method       TEXT NOT NULL,
    path         TEXT NOT NULL,
    status_code  INTEGER,
    external_user_id BIGINT,
    gb_requested NUMERIC(14, 3),
    latency_ms   INTEGER,
    ip           TEXT,
    correlation_id TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_reqlog_created ON api_requests_log(created_at DESC);
```

Правила инвариантов:
- `partners.balance_gb` **никогда** не уходит в минус. Проверка в SQL: `UPDATE ... WHERE balance_gb >= $1`.
- Каждое изменение `balance_gb` записывается в `partner_transactions` в той же транзакции.
- `partner_users` уникален по `(partner_id, external_user_id)` — идемпотентность.

---

## 5. HTTP API-контракт (Broker ↔ Партнёрский проект)

### 5.1 Аутентификация

Заголовок `X-Partner-Key: <plain_key>` в каждом запросе. Broker берёт этот ключ,
`argon2.verify(hash, plain)` против `partners.api_key_hash`. Кэш положительной
проверки в памяти на 60 секунд (dict `plain_key_prefix_hash → partner_id`),
чтоб argon2 не жёг CPU на каждый запрос.

Возвращаемые статусы:
- Нет заголовка → `401 unauthorized`
- Не совпал ключ / партнёр disabled → `403 forbidden`

### 5.2 Идемпотентность

Клиент (партнёр) **должен** передавать `X-Idempotency-Key` в каждом write-запросе.
Значение — любая уникальная строка, ≤ 128 символов (мы советуем `UUIDv4`).

Если Broker уже видел этот ключ у этого партнёра в последние 24 часа — возвращает
кешированный ответ (реплей). Иначе обрабатывает и кеширует.

Реализация: таблица `idempotency_keys(partner_id, key, response_body, created_at)`
+ автоочистка старше 24 часов.

Также **отдельная** идемпотентность на уровне бизнес-ключа `(partner_id, external_user_id)`
— описана ниже в `POST /create`.

### 5.3 Эндпоинты

#### `POST /api/v1/partner/allocate`

Основной эндпоинт: «выдай юзеру X ГБ на его подписку обхода». Работает и как
создание нового юзера, и как добор ГБ существующему.

**Заголовки:**
- `X-Partner-Key: <plain_key>` — обязательно
- `X-Idempotency-Key: <string>` — обязательно
- `Content-Type: application/json`

**Тело:**
```json
{
  "external_user_id": 328243408,
  "gb_amount": 10
}
```

- `external_user_id`: Telegram-id конечного юзера в партнёрском боте. Integer > 0.
- `gb_amount`: сколько ГБ добавить. Number > 0, точность до 0.001 ГБ (для тестов).

**Успешный ответ 200:**
```json
{
  "ok": true,
  "action": "created",           // "created" | "topped_up"
  "subscription_url": "https://sub.atlas-secure.ru/xxxxxxx",
  "remnawave_uuid": "9f2e...c1a",
  "remnawave_username": "partner_328243408",
  "total_gb_allocated": 10.0,    // накопительно у этого юзера
  "broker_balance_after_gb": 20.0,  // сколько ГБ осталось у партнёра
  "correlation_id": "brk_a1b2c3"
}
```

**Логика по бизнес-ключу `(partner_id, external_user_id)`:**
- Если записи в `partner_users` **нет** → создаём энтити в Remnawave,
  `action="created"`.
- Если запись **есть** → делаем topup: `remnawave_api.update_user(uuid,
  trafficLimitBytes = current + gb_amount·1024³)`, `action="topped_up"`.
  URL остаётся прежний.

**Ошибки:**

| Код | Body | Когда |
|---|---|---|
| 400 | `{"error":"invalid_request","detail":"..."}` | некорректное тело / отрицательный gb |
| 401 | `{"error":"unauthorized"}` | нет X-Partner-Key |
| 403 | `{"error":"forbidden"}` | ключ не подошёл / partner disabled |
| 402 | `{"error":"insufficient_balance","balance_gb":8.5,"requested_gb":10}` | не хватает у партнёра — **без списания** |
| 409 | `{"error":"idempotency_conflict"}` | тот же X-Idempotency-Key с другим телом |
| 429 | `{"error":"rate_limited","retry_after":5}` | превышен rps |
| 502 | `{"error":"remnawave_upstream","detail":"..."}` | панель не ответила — **balance откачен** |
| 503 | `{"error":"broker_db_unavailable"}` | Broker-БД лежит |

**Что делает Broker внутри (алгоритм):**

```
BEGIN;

  -- 1. auth + rate-limit (уже проверено middleware'ом)

  -- 2. идемпотентность по X-Idempotency-Key
  SELECT response_body FROM idempotency_keys
    WHERE partner_id=$p AND key=$k AND created_at > now() - '24h'::interval;
  -- если найдено → вернуть из кеша (без транзакции даже, до BEGIN)

  -- 3. существующий юзер?
  SELECT * FROM partner_users
    WHERE partner_id=$p AND external_user_id=$e
    FOR UPDATE;

  -- 4. balance check + atomic debit
  UPDATE partners
     SET balance_gb = balance_gb - $gb
     WHERE id = $p AND balance_gb >= $gb
     RETURNING balance_gb;
  -- если 0 rows → ROLLBACK; вернуть 402

  INSERT INTO partner_transactions (partner_id, kind, gb_delta, balance_after,
                                    external_user_id, correlation_id)
    VALUES ($p, 'debit', -$gb, $balance_after, $e, $corr);

COMMIT;

-- 5. Remnawave call — вне транзакции
IF существующий_юзер:
    result = remnawave_api.update_user(uuid,
        trafficLimitBytes = current + gb_bytes)
ELSE:
    result = remnawave_api.create_user(
        username=f'partner_{external_user_id}',
        traffic_limit_bytes=gb_bytes,
        expire_at=now + 10y,
        squad_uuid=BYPASS_SQUAD_UUID,
        traffic_limit_strategy='NO_RESET',
    )

-- 6. Если Remnawave упал → refund
IF NOT result.ok:
    BEGIN;
      UPDATE partners SET balance_gb = balance_gb + $gb WHERE id=$p;
      INSERT INTO partner_transactions (kind='refund', gb_delta=+$gb,
                                        ref_note='remnawave_upstream_error');
    COMMIT;
    return 502;

-- 7. Успех — INSERT / UPDATE в partner_users
IF существующий_юзер:
    UPDATE partner_users SET total_gb_allocated = total_gb_allocated + $gb,
                             last_topup_at = now()
      WHERE id = $partner_user_id;
ELSE:
    INSERT INTO partner_users (partner_id, external_user_id, remnawave_uuid,
                               remnawave_username, subscription_url,
                               total_gb_allocated)
      VALUES (...);

-- 8. Кеш идемпотентности
INSERT INTO idempotency_keys (partner_id, key, response_body)
  VALUES ($p, $k, $response);

-- 9. Пост-проверка: не упал ли баланс ниже 20% от last_topup?
IF balance_gb < 0.2 * last_topup_gb AND NOT low_balance_alert_sent:
    -- отправить Telegram-алерт админу
    UPDATE partners SET low_balance_alert_sent = TRUE WHERE id=$p;

RETURN 200;
```

#### `GET /api/v1/partner/user/{external_user_id}`

Получить состояние одного юзера у себя — сколько GB накоплено, какой URL,
жив ли он ещё.

**Ответ 200:**
```json
{
  "external_user_id": 328243408,
  "remnawave_uuid": "9f2e...c1a",
  "subscription_url": "https://sub.atlas-secure.ru/xxx",
  "total_gb_allocated": 30.0,
  "traffic_used_gb": 12.4,       // из панели, LIVE
  "traffic_remaining_gb": 17.6,
  "created_at": "2026-07-01T14:23:00Z",
  "last_topup_at": "2026-08-01T09:11:00Z"
}
```

Ошибки:
- `404 not_found` — юзера у этого партнёра нет.
- `502 remnawave_upstream` — не удалось прочитать `traffic_used` из панели.

#### `GET /api/v1/partner/balance`

Просто вернуть текущий баланс партнёра.

**Ответ 200:**
```json
{
  "balance_gb": 22.5,
  "last_topup_gb": 30.0,
  "low_balance_threshold_gb": 6.0
}
```

#### `GET /api/v1/health`

Без авторизации. Для liveness/readiness.

**Ответ 200:**
```json
{"ok": true, "db": "up", "remnawave": "up"}
```

Если что-то down — статус 503 + детали.

---

## 6. Telegram-бот часть (aiogram)

### 6.1 Роли

- **Admin** (`ADMIN_TELEGRAM_ID` из env): управляет всеми партнёрами, пополняет
  балансы, видит транзакции.
- **Partner** (`partners.telegram_id`): пишет боту `/mybalance` и получает
  push-алерты о низком балансе.

### 6.2 Admin-команды

```
/partners
  → список партнёров: id, name, tg_id, balance_gb

/topup <partner_id> <gb> [note]
  → пополнить баланс. Пример: /topup 1 30 «оплата 10.07 карта»
  → атомарно: partners.balance_gb += gb, INSERT в partner_transactions (kind=topup)
  → сбрасывает флаг low_balance_alert_sent
  → устанавливает last_topup_gb = gb
  → отвечает: «✅ partner_id=1 пополнен на 30 ГБ. Баланс: 30 ГБ»

/balance <partner_id>
  → тот же вывод что и HTTP GET /balance, плюс last 10 транзакций

/history <partner_id> [limit=50]
  → выгрузить partner_transactions в текстовое сообщение или CSV

/rotate_key <partner_id>
  → сгенерировать новый api-key (32 байта, base64url), показать один раз
  → обновить partners.api_key_hash
  → залогировать в partner_transactions (kind='key_rotation', gb_delta=0)

/disable <partner_id>
  → soft-delete: partners.disabled_at = now()
  → все последующие вызовы вернут 403

/create_partner <telegram_id> <name>
  → INSERT в partners, вернуть новый api-key одним сообщением с warning
    «сохрани в password-manager — второй раз не покажу»
```

### 6.3 Partner-команды

```
/mybalance
  → вернуть balance_gb + порог алерта.
  → доступна ТОЛЬКО из чата с tg_id, совпадающим с partners.telegram_id.

/start
  → приветствие + краткая инструкция «твой API-ключ у @admin, порог алерта 20%».
```

### 6.4 Push-алерты админу

При каждом из событий Broker шлёт сообщение в чат с `ADMIN_TELEGRAM_ID`
+ дубль в чат `partner.telegram_id` (если это разные id):

1. **Баланс упал ниже 20% от last_topup_gb.** Один раз, пока не пополнят.
   ```
   ⚠️ Партнёр «acme» (id=1): баланс упал до 5.4 ГБ (было 30, порог 6).
   Пополни через /topup 1 <N>.
   ```

2. **Отказ по 402 (нехватка баланса).** Каждый раз, но не чаще чем раз в 5 минут
   (rate-limit).
   ```
   🚫 Партнёр «acme» (id=1): попытка списать 10 ГБ, доступно 3. Юзер 328243408
   не получил ссылку. Пополни срочно.
   ```

3. **Remnawave upstream упал.** Каждый раз, но не чаще чем раз в 2 минуты.
   ```
   🔥 Remnawave не отвечает. Partner allocate вернулся 502.
   Refund выполнен, partner=1, юзер 328243408, 10 ГБ возвращены.
   ```

---

## 7. Интеграция с Remnawave

Broker использует **те же** credentials, что и Atlas:
`REMNAWAVE_API_URL`, `REMNAWAVE_API_TOKEN`.

Копипастить `app/services/remnawave_api.py` из Atlas-репо не нужно — есть два
варианта:
- **Вариант А (проще)**: тонкий httpx-клиент прямо в Broker, реализующий ровно
  два вызова: `POST /api/users`, `PATCH /api/users/{uuid}`, `GET /api/users/{uuid}`.
- **Вариант Б (правильнее)**: вынести Atlas-код `remnawave_api.py` в отдельную
  Python-либу и подключить в обоих сервисах.

Для MVP → вариант А.

### 7.1 Формат create-запроса

```python
POST /api/users
{
    "username": f"partner_{external_user_id}",
    "shortUuid": <randomly-generated 12-char>,
    "trafficLimitBytes": int(gb * 1024**3),
    "expireAt": "2036-01-01T00:00:00Z",    # +10 лет
    "activeUserInbounds": [],               # не нужны для bypass
    "activeInternalSquads": [BYPASS_SQUAD_UUID],
    "trafficLimitStrategy": "NO_RESET",     # НЕ сбрасывать при renewal cycle
    "description": f"partner_id={partner_id}, ext_id={external_user_id}",
    "telegramId": external_user_id,         # для наглядности в панели
}
```

Ответ Remnawave содержит `uuid`, `subscriptionUrl`, `shortUuid`. Broker сохраняет.

### 7.2 Формат topup-запроса

```python
PATCH /api/users/{uuid}
{
    "trafficLimitBytes": <current_bytes + additional_bytes>,
    "status": "ACTIVE",
}
```

Всегда переводим в ACTIVE — если юзер был DISABLED (закончился трафик), после
топ-апа снова оживает.

### 7.3 Namespace-фильтр в Atlas

**Обязательное изменение в Atlas-репо** (отдельным PR):
- `get_all_users`-каллеры, которые считают статистику панели, должны
  фильтровать `username NOT LIKE 'partner_%'`.
- Наш watchdog (`app/services/subscription_watchdog.py`) уже безопасен —
  partner-юзеры не идут через `grant_access`.
- Наша «Сверка» (`database/reconciliation.py`) уже безопасна — regex
  `^tg_(\d+)_premium$`.

Список конкретных файлов и патчей — в отдельном issue после MVP-запуска.

---

## 8. Rate-limits и security

### 8.1 API rate-limit

- Per partner-key: **30 req/sec, burst 60**, реализовать через `slowapi` или
  ручной sliding-window в Redis. Для MVP — in-memory dict + `asyncio.Lock`.
- Health-endpoint не лимитируется.

### 8.2 Хранение api-key

- **Never plain**: `argon2.PasswordHasher().hash(plain)` — 96 хешей/сек на одном
  ядре, для нас достаточно.
- Первые 8 символов plain-ключа сохраняем в `partners.api_key_prefix` для
  быстрого поиска и логгирования (лог пишем префикс, никогда не полный).

### 8.3 IP whitelisting (опционально)

- Env `PARTNER_ALLOWED_IPS` — comma-separated `1.2.3.4,5.6.7.8`. Пусто →
  разрешены все. Если указано — 403 остальным.

### 8.4 HTTPS

- Broker **всегда** за HTTPS-прокси (nginx / Caddy). Локально может слушать
  `127.0.0.1:8080`, прокси терминирует TLS.

### 8.5 Логи и приватность

- Плейн api-key в логи не пишем **никогда**.
- external_user_id в логи пишем — это не PII (Telegram id уровень нормальный
  для наших админ-логов).
- Идемпотентные ключи в логи — только префикс 8 символов.

---

## 9. Observability

### 9.1 Логи

Structured JSON в stdout. Обязательные поля каждого события:
```json
{
  "ts": "ISO-8601",
  "level": "info|warn|error",
  "correlation_id": "brk_a1b2c3",  // сквозной id request → transaction → alert
  "event": "allocate_success | allocate_402 | remnawave_fail | ...",
  "partner_id": 1,
  "external_user_id": 328243408,
  "gb": 10,
  "balance_after": 20.0,
  "latency_ms": 187
}
```

### 9.2 Метрики (опционально)

Prometheus `/metrics`:
- `broker_allocate_total{partner_id, action, status}` — counter
- `broker_allocate_latency_seconds{partner_id}` — histogram
- `broker_balance_gb{partner_id}` — gauge
- `broker_remnawave_upstream_errors_total` — counter

Для MVP можно опустить — оставить только логи.

### 9.3 Алерты (уже описаны в §6.4)

Дублируются в лог с `event="admin_alert"`.

---

## 10. Failure-моды и восстановление

| Сценарий | Что делает Broker | Что видит партнёр |
|---|---|---|
| Broker упал между `UPDATE balance` и Remnawave-вызовом | При старте сверяет `partner_transactions` — если есть `debit` без парного `partner_users.created_at` в течение 60 сек → refund | Партнёр получил timeout → ретрайнет по X-Idempotency-Key → получит либо кешированный ответ, либо повторит попытку |
| Remnawave вернул 5xx | Refund баланса + 502 партнёру | Партнёр может ретрайнуть через 2-5 сек |
| Broker-БД упала | 503 партнёру, ничего не делаем | Партнёр ретрайнет позже |
| Партнёр отправил NaN или отрицательный gb | 400 | Багфикс у партнёра |
| Партнёр повторил `X-Idempotency-Key` с другим `gb` | 409, объясняем в теле | Багфикс у партнёра |

### 10.1 Recovery-скрипт (запускать при подозрении на рассинхрон)

```python
# scripts/reconcile_balance.py
# Пересчитывает balance_gb из partner_transactions и сравнивает с partners.balance_gb.
# Расхождение > 0.01 ГБ — фейлит и печатает список расхождений.
```

---

## 11. Environment variables

### 11.1 Broker-бот (.env)

```dotenv
# ─── Telegram ─────────────────────────────────────────────────────────
BROKER_BOT_TOKEN=<новый BOT_TOKEN, отдельный от Atlas>
ADMIN_TELEGRAM_ID=<твой Telegram id — приходят алерты>

# ─── Database ─────────────────────────────────────────────────────────
DATABASE_URL=postgres://broker:PASSWORD@localhost:5432/broker
DB_POOL_MIN=2
DB_POOL_MAX=10

# ─── HTTP server ──────────────────────────────────────────────────────
HTTP_HOST=127.0.0.1
HTTP_PORT=8080
# Пустое = разрешить все IP. Заполнено = whitelist.
PARTNER_ALLOWED_IPS=

# ─── Remnawave ────────────────────────────────────────────────────────
# Те же значения, что у Atlas (или отдельный token с scope на POST/PATCH users).
REMNAWAVE_API_URL=https://panel.atlas-secure.ru
REMNAWAVE_API_TOKEN=<jwt-token>
REMNAWAVE_BYPASS_SQUAD_UUID=<uuid squad'а Clients>
# expireAt для новых партнёрских энтити — сколько лет вперёд
BYPASS_EXPIRE_YEARS=10

# ─── Business rules ──────────────────────────────────────────────────
LOW_BALANCE_THRESHOLD_PERCENT=20        # порог алерта
INSUFFICIENT_ALERT_MIN_INTERVAL_SEC=300 # анти-спам 402-алертов
UPSTREAM_ALERT_MIN_INTERVAL_SEC=120

# ─── Rate limiting ────────────────────────────────────────────────────
RATE_LIMIT_PER_KEY_PER_SEC=30
RATE_LIMIT_BURST=60

# ─── Idempotency ──────────────────────────────────────────────────────
IDEMPOTENCY_TTL_HOURS=24

# ─── Logging ──────────────────────────────────────────────────────────
LOG_LEVEL=info
LOG_FORMAT=json
```

### 11.2 Партнёрский проект (env партнёра)

Что партнёру нужно записать у себя в `.env`:

```dotenv
# Broker endpoint
BROKER_API_URL=https://broker.atlas-secure.ru
# Ключ, выданный админом в чате Broker-бота (команда /rotate_key).
# Никому не показывать, хранить как секрет.
BROKER_API_KEY=aB3xK9pQ4mZ2rTuVw7YnJhLg5FdKvE8mS1DcXbNpOqR

# Опционально — таймаут HTTP-запроса. Broker должен отвечать < 500 мс,
# ставим 3 сек с запасом.
BROKER_HTTP_TIMEOUT_SECONDS=3
```

---

## 12. Псевдо-код для партнёрского проекта

Для наглядности — что должен сделать партнёрский бот, когда его юзер оплатил
пакет:

```python
import httpx, uuid, os

BROKER_URL = os.environ["BROKER_API_URL"]
BROKER_KEY = os.environ["BROKER_API_KEY"]
TIMEOUT = float(os.environ.get("BROKER_HTTP_TIMEOUT_SECONDS", 3))


async def issue_bypass_to_user(user_tg_id: int, gb: int) -> dict:
    """Called after the partner user has paid.
    Returns {'ok': True, 'url': '…'} on success or {'ok': False, 'reason': '…'}.
    """
    idempotency_key = f"partner-{user_tg_id}-{uuid.uuid4()}"
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            r = await client.post(
                f"{BROKER_URL}/api/v1/partner/allocate",
                headers={
                    "X-Partner-Key": BROKER_KEY,
                    "X-Idempotency-Key": idempotency_key,
                    "Content-Type": "application/json",
                },
                json={
                    "external_user_id": user_tg_id,
                    "gb_amount": gb,
                },
            )
        except httpx.RequestError as e:
            return {"ok": False, "reason": f"network: {e}"}

    if r.status_code == 200:
        data = r.json()
        return {"ok": True, "url": data["subscription_url"]}
    if r.status_code == 402:
        # У Broker'а закончился баланс — админ уже получил алерт.
        return {"ok": False, "reason": "broker_out_of_stock"}
    if r.status_code == 502:
        # Remnawave down — партнёр может ретрайнуть
        return {"ok": False, "reason": "upstream_temp_error", "retryable": True}
    return {"ok": False, "reason": f"http_{r.status_code}", "body": r.text[:200]}
```

**Что делает партнёр после ответа:**
- `ok=True` → отдаёт `url` юзеру, показывает QR-код, готово.
- `broker_out_of_stock` → пишет юзеру «⏳ сервис временно недоступен, скоро
  вернёмся» и оставляет платёж на возврат / отложенное исполнение (это
  бизнес-решение партнёра).
- `upstream_temp_error` → ретрайнуть через 5-15 сек с тем же
  `X-Idempotency-Key` — Broker сам не создаст дубль благодаря идемпотентности.

---

## 13. Приёмочные критерии MVP

- [ ] `POST /allocate` укладывается в 500 мс p95 (при живом Remnawave).
- [ ] При недостатке баланса возвращается 402, ничего не списывается.
- [ ] Повторный `POST /allocate` с тем же `(external_user_id, key)` возвращает
      кешированный ответ, ничего не списывается.
- [ ] Тот же `external_user_id`, но новый `X-Idempotency-Key` → топ-ап той же
      энтити, тот же URL, +N ГБ в панели.
- [ ] Ремнавейв возвращает 5xx → баланс восстановлен (проверить в
      `partner_transactions` — есть debit + refund с той же `correlation_id`).
- [ ] Порог 20% срабатывает один раз до пополнения.
- [ ] `/topup` меняет баланс атомарно и сбрасывает флаг low_balance.
- [ ] `/rotate_key` инвалидирует старый ключ мгновенно.
- [ ] Logs — JSON, correlation_id проходит сквозным от request до alert.

---

## 14. Что вне scope MVP (можно потом)

- Multi-partner (сейчас один, но структура БД уже допускает).
- Автоматическое биллинг-снятие (сейчас админ вручную топ-ап).
- Webhook от Broker в партнёрский проект «твой юзер X исчерпал трафик».
- Партнёрский dashboard (HTTP-панель с историей).
- Возврат ГБ (refund_partner_user) — если юзер партнёра отменил заказ.
- Мульти-region подписки (пока `partner_<id>` живёт в одном squad).

---

## 15. Что дальше

1. Апрув ТЗ.
2. Разработчик заводит новый репо `atlas-broker`.
3. Реализация по разделам 4→12.
4. Мы (Atlas-side) катим отдельный PR с фильтром `partner_%` в бухгалтерских
   отчётах панели (см. §7.3).
5. Партнёру передаётся `BROKER_API_URL` + `BROKER_API_KEY` (последний — один
   раз в защищённом канале).
6. Прогон приёмочных тестов из §13 на staging → прод.
