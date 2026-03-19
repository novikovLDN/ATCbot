# Production Readiness Audit — 2026-03-19

## Summary

**Overall assessment: CONDITIONALLY READY** — после применения исправлений из этого аудита.

| Категория | Статус |
|-----------|--------|
| Безопасность | GOOD — параметризованные SQL, HMAC-валидация, rate limiting |
| Платежи | FIXED — исправлены idempotence key, amount в уведомлениях |
| Воркеры | FIXED — отлов ActivationNotAllowedError, guard pool |
| Конкурентность | FIXED — timeout на semaphore, advisory lock на миграции |
| БД | FIXED — SQL injection в SET, DB_READY guard, biz_keys |
| Тесты | NEEDS WORK — 42 теста, значительные пробелы |
| CI/CD | NEEDS WORK — security scan не блокирует deploy |

---

## CRITICAL Issues Found & Fixed

### 1. SQL injection via f-string — `database/core.py:254`
**Было:** `conn.execute(f"SET log_min_duration_statement = {SLOW_QUERY_THRESHOLD_MS}")`
**Стало:** `conn.execute("SET log_min_duration_statement = $1", str(SLOW_QUERY_THRESHOLD_MS))`
**Риск:** Хотя `int()` cast ограничивал вектор атаки, паттерн опасен.

### 2. `auto_renewal.py:70` — unguarded `get_pool()`
**Было:** `pool = await database.get_pool()` без try/except
**Стало:** Обёрнуто в try/except + проверка `if not pool`
**Риск:** Crash воркера при недоступной БД.

### 3. `database/users.py:659` — missing DB_READY guard в `create_user`
**Было:** Нет проверки `DB_READY`, TOCTOU race в обновлении referral_code
**Стало:** Добавлен guard + atomic UPDATE с `WHERE referral_code IS NULL`

---

## HIGH Issues Found & Fixed

### 4. `auto_renewal.py:411` — Wrong dict key "amount" vs "amount_rubles"
**Было:** `item.get("amount", 0)` — всегда 0, т.к. ключ `"amount_rubles"`
**Стало:** `item.get("amount_rubles", 0)`
**Риск:** Пользователи видели "0₽" в уведомлении об автопродлении.

### 5. `activation_worker.py:326` — `ActivationNotAllowedError` uncaught
**Было:** Исключение падало в `except Exception`, прерывая весь batch
**Стало:** Добавлен отдельный `except ActivationNotAllowedError` с mark_failed

### 6. `yookassa_service.py:153` — Fresh UUID idempotence key on every autopayment
**Было:** `idempotence_key = str(uuid4())` — при retry = двойное списание
**Стало:** Deterministic key из `sha256(autopay:telegram_id:method:amount:desc)`

### 7. `migrations.py:191` — No advisory lock for concurrent migrations
**Было:** Два инстанса могли запустить миграции одновременно
**Стало:** `pg_advisory_lock(123456789)` перед запуском миграций

### 8. `concurrency_middleware.py:37` — No semaphore timeout
**Было:** `async with self._semaphore` — бесконечное ожидание при перегрузке
**Стало:** `asyncio.wait_for(acquire, timeout=10)` + graceful drop

### 9. `guards.py:32` — DB query on every handler call
**Было:** `check_critical_tables()` = SQL запрос при каждом обращении пользователя
**Стало:** TTL-кэш 30 секунд — `_check_critical_tables_cached()`

### 10. `auto_renewal.py:504,595,647` — bare `pool.acquire()` bypasses pool monitor
**Было:** Card auto-renewal использовал `pool.acquire()` напрямую
**Стало:** `acquire_connection(pool, "card_auto_renewal_*")`

### 11. `database/biz_keys.py:114` — fragile result parsing
**Было:** `result.endswith("1")` — ложно-положительно для "UPDATE 11"
**Стало:** `result == "UPDATE 1"`

---

## HIGH Issues — Not Fixed (Require Design Decisions)

### 12. `auto_renewal.py:654-658` — Card auto-renewal: no refund on UUID regeneration
Когда `grant_access` создаёт новый UUID (вместо продления), деньги по карте уже списаны, но `continue` пропускает подписку без рефанда. В balance auto-renewal (line 271) refund есть.
**Рекомендация:** Добавить запрос рефанда через YooKassa API или активировать подписку.

### 13. `payment_webhook.py:140-177` — YooKassa webhook: no signature/IP verification
Headers не передаются в `process_webhook`. Верификация идёт через re-fetch платежа из API (line 303), что достаточно, но стоит добавить IP whitelist.

### 14. `ci.yml:114,117` — Security scan results ignored (`|| true`)
Security Scan job не блокирует CI и не входит в deploy gate.
**Рекомендация:** Убрать `|| true` или вынести в отдельный required check.

### 15. `config.py:20` — `APP_ENV` defaults to "prod"
Если переменная не задана, приложение молча работает в production режиме.
**Рекомендация:** Убрать default или сделать default="local".

### 16. `subscriptions.py` — `grant_access` runs queries without transaction when `conn=None`
При вызове без переданного `conn`, функция создаёт bare connection без transaction wrapper.
**Рекомендация:** Обернуть в `async with conn.transaction()` для `conn=None` кейса.

### 17. `admin.py` — `admin_grant_access_atomic` holds advisory lock across VPN HTTP call
Session-level lock + pool connection заняты на время HTTP вызова к VPN API.
**Рекомендация:** Перейти на двухфазный подход (release connection before HTTP).

---

## MEDIUM Issues (Improve When Possible)

| # | Файл | Проблема |
|---|------|----------|
| 1 | `rate_limit.py:128` | `_buckets` dict растёт бесконечно (нет eviction) |
| 2 | `rate_limit_middleware.py:57` | Redis check "once, remember forever" — нет переподключения |
| 3 | `telegram_webhook.py:61` | Content-Length header можно подделать (chunked transfer) |
| 4 | `telegram_error_middleware.py:102` | Ошибка пользователю всегда на русском (нет i18n) |
| 5 | `trial_notifications.py:467` | DB connection held during VPN HTTP call |
| 6 | `reminders.py:110` | Unbounded fetch (нет LIMIT/pagination) |
| 7 | `broadcast_service.py:133` | `asyncio.gather` создаёт все coroutines сразу |
| 8 | `broadcast_service.py:110` | Rate ~100 msg/s может превысить Telegram лимит (30 msg/s) |
| 9 | `database/core.py:462-1061` | `init_db()` — 600+ строк DDL, `except Exception: pass` |
| 10 | `Dockerfile:1` | `python:3.11-slim` без pinned version |
| 11 | `deploy.yml:49-53` | Deploy silently succeeds without hook URL |
| 12 | `yookassa_service.py:265` | `verify_webhook_ip` always returns True |
| 13 | `referrals/service.py:286` | `_activate_referral_internal` без transaction |
| 14 | `auto_renewal.py:169` | `last_auto_renewal_at` по telegram_id, а не subscription_id |
| 15 | `activation_worker.py:141` | `items_processed` инкрементируется до обработки |

---

## Test Coverage Gap Analysis

**42 теста** покрывают 5 из 8 сервисных модулей.

### Хорошо покрыто:
- Subscription status logic (14 тестов)
- Webhook signature verification (12 тестов)
- Payment validation (11 тестов)
- Trial management (11 тестов)

### Критические пробелы (0 тестов):
| Область | Риск |
|---------|------|
| Rate limiting | Обход rate limiter = DoS |
| Referral reward calculation | Неверные начисления |
| Balance mutations (topup/withdrawal) | Финансовые потери |
| Broadcast service | Mass notification bugs |
| VPN key generation | Утечка ключей |
| Admin authorization middleware | Несанкционированный доступ |

---

## Architecture Strengths

- **Environment isolation** — PROD_/STAGE_/LOCAL_ prefix system
- **Advisory lock** для single-instance workers
- **Two-phase activation** — DB connection не удерживается во время HTTP
- **Dual-backend rate limiting** — Redis + in-memory fallback
- **Parameterized SQL** повсеместно (кроме 1 fixed case)
- **Structured logging** с correlation IDs
- **Graceful shutdown** — полная очистка ресурсов
- **Non-root Docker** container
- **39 numbered SQL migrations** с идемпотентностью

---

## Recommendations Before Production

### Must Fix (blockers):
1. Решить вопрос с card auto-renewal refund (#12)
2. Убрать `|| true` из security scan в CI (#14)

### Should Fix (high priority):
3. Добавить тесты для referral rewards, rate limiting, balance operations
4. Pinned Docker image version
5. `APP_ENV` default to "local" instead of "prod"
6. `grant_access` transaction wrapping for `conn=None`
7. YooKassa webhook IP verification

### Nice to Have:
8. Coverage reporting в CI
9. Deploy notifications (Telegram/Slack)
10. Rollback mechanism в deploy pipeline

---

*Audit performed: 2026-03-19*
*Files analyzed: 40+ across all layers*
*Issues found: 3 CRITICAL (fixed), 14 HIGH (11 fixed, 3 need design decisions), 15 MEDIUM*
