# RUNBOOK: выкатка `refactor/audit-2026-09` и включение платёжного ядра

- Дата: 2026-09-13. База: прод `main` = `cf8205fc`. Ветка: `refactor/audit-2026-09` (`73a38011`, 105 коммитов поверх `cf8205fc`).
- Источники: `docs/audit/SCOPE.md`, `docs/audit/00_recon.md`, `docs/audit/02_payment_core_plan.md`, `docs/audit/05_prod_readiness.md` (проверка готовности с доказательствами), `docs/dashboard/auth.md`, `docs/dashboard/metrics.md`, `app/services/payments/CLAUDE.md`.
- **В `main` мержит только владелец** (`SCOPE.md` §«База и ветки»). Force-push запрещён.
- Все SQL ниже — PostgreSQL. Колонки `provisioning_jobs` — `TIMESTAMP` в UTC: сравнивать с `now() AT TIME ZONE 'UTC'`. Исключение — `payment_errors.created_at`, это `TIMESTAMPTZ` (`migrations/055_payment_errors.sql`).

---

## 0. Коротко: чек-листы

Подробности и доказательства — `docs/audit/05_prod_readiness.md`. Вердикт проверки: **готово с условиями**, блокеров в коде нет.

**До деплоя**

1. [ ] `CI OK` на GitHub для ветки или PR. Ветка ни разу не пушилась, а `Docker Build` и `Frontend Build` локально не проверены.
2. [ ] Живые рекуррентные подписки Platega отменены в кабинете (2.3).
3. [ ] WATA: действующий токен, `PROD_WATA_SANDBOX=false`, ключ закреплён или выбрана ленивая загрузка (2.4).
4. [ ] `PROD_JWT_SECRET` и `PROD_DASHBOARD_BASE_URL` (точный origin) заданы, у админа есть пароль или passkey (2.7, 2.8).
5. [ ] Прокси подписок не используется (2.2).
6. [ ] `schema_migrations`: верх `080`, `082` нет. Размер `users`, `subscriptions`, `audit_log` проверен (2.6, таймаут 30 с).
7. [ ] Дамп БД вне Railway (2.5).
8. [ ] `PROD_USE_NEW_PROVISIONING` **не задана** (по умолчанию новое ядро на всех точках входа, решение 2026-09-14), `PROD_NEW_PROVISIONING_ENTRYPOINTS` пусто.

**После деплоя (15 минут)**

- [ ] Логи: `Applying migration 081/082` → `Advisory lock acquired` → `PROVISIONING_WORKER started` → `WATA_PUBLIC_KEY_WARMUP: ok=True` → `WEBHOOK_VERIFIED` → `UVICORN_STARTED`. Нет `Migration … FAILED`, `DB INIT FAILED`, `CRITICAL`.
- [ ] `/health` → 200 `"status":"ok"`, `POST /webhooks/lava` → 404 (3.4, 2.1).
- [ ] Бот, дашборд, тестовая покупка — по 3.5.
- [ ] Алерты в Telegram админа: нет неожиданных `PAYMENT ALERT` / `PROVISIONING`.

**Откат** — 6.2: `USE_NEW_PROVISIONING=off` → Redeploy предыдущего деплоя (`cf8205fc`) → **БД не откатывать**. Проверено, что код `main` стартует на обновлённой БД и ничего в ней не меняет.

---

## 1. Что выкатываем

### 1.1. Изменения по областям (`git log --oneline cf8205fc..fa0c2774`)

| Область | Что сделано | Ключевые коммиты |
|---|---|---|
| **Платежи, волна 1 (без флага)** | WATA: подпись webhook fail-closed, проверка суммы и валюты, алерты на Refund и 401. Прогрев публичного ключа при старте. WATA-reconciler: документированный поиск транзакции, скан только по своему провайдеру. Platega: разбор колбэков по общему URL, алерты на суммы, chargeback и сирот | `65253312`, `633622f6`, `2eb2b9a8`, `ee08f360` |
| **T6: вебхуки, P0 (без флага)** | Блокирующий `FOR UPDATE` внутри транзакции и advisory lock на покупку: дубль вебхука получает `already_processed`. Единая таблица HTTP-кодов `_STATUS_HTTP`: `TransientPaymentError` → **500** (провайдер повторит). Проверка провайдера покупки (`provider_mismatch`). Пополнение баланса зачисляет **ожидаемую** сумму | `7129d056` |
| **Lava** | Hotfix: webhook отключён (`6827db72`). Затем Lava удалена целиком: `/webhooks/lava` → 404, кнопки WATA гейтятся `wata_service.is_enabled()` | `6827db72`, `4ea51867` |
| **Чистка кода** | B1 — старые доки в `docs/archive/`. B2 — мёртвые модули. B3 — хэндлеры-сироты и общий fallback. B5 — samopis, `vpn_utils`, `subscription_proxy`. Удалён `site_sync` | `8c9bf2f6`, `e7cbf594`, `e4f038c3`, `5544d851`, `c5f2b1e7` |
| **Платёжное ядро (за флагом)** | Каталог тарифов (T1). Outbox `provisioning_jobs`, миграция 082 (T2). Сервис выдачи с CAS по bypass, срок premium никогда не сокращается (T4). Воркер (T5). Отложенная выдача (T7). Точки входа: webhook (T8), telegram (T9), balance (T10), autorenew (T12), admin (T13), gift (T14), trial (T15), grants (T16). Агрегация алертов очереди | `5cef54f4` … `267fa8fe`, `7b11ca83` |
| **Фиксы без флага** | Двойное списание с баланса при двойном тапе (`c107acb0`). `NameError days_int` в админ-выдаче минутами (`945a3ff3`). Продление premium при подарке активному пользователю (`4d6a0963`). Spotify, оплаченный картой через Telegram, завершается как Spotify (`404cd66e`). Тексты подарка и реферала для шеринга (`e6a8ab6b`). Бот для ссылок берётся из `config.BOT_USERNAME` (`0a70e4df`) | — |
| **Рекуррент Platega** | **Код удалён** (`9549e706`). Колбэк по подписке → лог, `payment_errors`, forced-алерт, ответ 200, ничего не выдаётся | `9549e706` |
| **Дашборд v2 → v3** | v2: Overview/Money, карточка пользователя, миграция 081 (индексы) — `04693aab`. v3: метрики `/metrics/*`, `/panel/*`, брендинг, усиление авторизации, Idempotency-Key, новые экраны | `04693aab`, `f602db5f`, `17581b8f`, `dd038845`, `98e68a3e`, `4bb424c5`, `f426341d`, `4ea4c3ac`, `82f1ff80`, `8f120244` |
| **Тесты и доки** | Характеризующие тесты платежей (T0), документы аудита и уведомлений | `197bbef1`, `fc22b25e`, `0b875057` и др. |

### 1.2. Что меняется для пользователей

**Сразу после деплоя** (флаг ядра выключен):
- Пополнение баланса зачисляет сумму счёта, а не присланную провайдером. Переплата (комиссия) не зачисляется, пишется лог `BALANCE_TOPUP_OVERPAYMENT` (`database/subscriptions.py:5125`).
- Двойной тап «Оплатить с баланса» больше не списывает деньги дважды.
- Кнопок «Карта (Lava)» больше нет. Кнопка WATA видна, если задан `WATA_ACCESS_TOKEN`.
- Кнопки «Подписка СБП (рекуррент)» больше нет (`pay:sbp_sub` удалён).
- Spotify, оплаченный картой в Telegram, получает сообщение об успехе Spotify.
- Подарок активному получателю продлевает premium в панели.

**При включении ядра по точкам входа** (раздел 4):
- ГБ начисляются ровно по каталогу: basic и plus +10, combo — по таблице combo (30 дней → +75). Нет больше лишних +10 или +75 (замеры T0: `02_payment_core_plan.md`, «Находки T0»).
- Покупка проходит даже при недоступной панели Remnawave: выдача встаёт в очередь, пользователь видит `payment.pending_activation`.
- **`autorenew`: combo-подписки продлеваются по цене combo (329 ₽ за 30 дней) и с ГБ combo.** Это осознанное изменение суммы списания (G0). Plus больше не превращается в basic. Уведомление «Списано 0.00 ₽» исправлено.
- Выдачи днями (админ, игра, промо, бонус) — **0 ГБ**, только premium (G0).
- Триал: 500 МБ bypass + 3 дня (`config.TRIAL_BYPASS_MB`).

### 1.3. Что меняется для админа

- **Вход в дашборд** (`docs/dashboard/auth.md`, `app/api/dashboard/auth.py:48` `MAGIC_TTL = 15 min`):
  - Ссылка из `/admin` живёт **15 минут** (было 30 дней). Она нужна **только** для первичной настройки или после сброса пароля.
  - Обычный вход — логин + пароль или passkey. Ссылка лишь открывает страницу входа.
  - **Старые 30-дневные ссылки перестают работать сразу после деплоя.**
  - Забыл пароль: в боте `/admin` → «🔄 Сбросить пароль». Стираются пароль, passkey и все сессии. Затем снова `/admin` → форма установки пароля (15 минут).
  - Сессия — cookie `atlas_admin_session`, 5 дней.
- **Блокировки** (`app/api/dashboard/security.py:132-133`):
  - `/auth/login`: 5 неудач на логин **или** 10 на IP за 15 минут → блокировка на 15 минут (429 + `Retry-After`). Во время блокировки не пройдёт даже верный пароль. Лог `DASHBOARD_AUTH_LOCKED`.
  - `/auth/setup`: 5 неудач с IP. Passkey: 10 неудач с IP.
  - Счётчики хранятся в Redis, без Redis — в памяти процесса (сбрасываются рестартом).
- **CSRF** (`app/api/dashboard/security.py:77-101`): мутация под `/dashboard/api` с `Origin` или `Referer`, не равным `DASHBOARD_BASE_URL` или собственному хосту, получает 403. То же при `Sec-Fetch-Site: cross-site`. Запрос **без** `Origin` и `Referer` получает 403, только если несёт cookie-сессию; без cookie он проходит, это curl или скрипт. WebSocket — только cookie + проверка `Origin`.
- **Idempotency-Key** на выдачи, баланс, рассылки и промо: двойной клик выполняет действие один раз.
- **Новые экраны v3** (`dashboard/src/App.tsx:110-134`, меню `Shell.tsx:23-44`): Обзор, Деньги, Подписчики, Панель, **Операции** (очередь выдачи, ошибки платежей, висящие счета), Настройки. Старые экраны доступны в меню как legacy.
- **Брендинг:** `BRAND_*` из env (`app/branding.py`, публичный `GET /dashboard/api/branding`). По умолчанию «Atlas Secure».
- **Рекуррент Platega:** живые подписки показывает админ-команда `/platega_sub_status` (`app/handlers/admin/base.py:139`).

---

## 2. Предварительные проверки (чек-лист)

> Подключение к прод-БД: `export DATABASE_URL='<PROD_DATABASE_URL из Railway>'` (для доступа снаружи — публичный URL Postgres-сервиса Railway). Далее `psql "$DATABASE_URL"`.

### 2.1. Lava (P0-A из recon)

Сейчас на проде (`main` = `cf8205fc`) роут `/webhooks/lava` **уязвим**: неподписанный POST может зачислить любую сумму (`00_recon.md` §P0-A).

- [ ] **PR #772** (`hotfix/disable-lava-webhook`, коммит `6827db72`) на 2026-09-13 **OPEN, не смержен** (`gh pr view 772 --json state` → `"OPEN"`). Эта ветка его содержит, и Lava в ней удалена целиком: проба `POST /webhooks/lava` → 404 (`05_prod_readiness.md`, проверка 3).
- [ ] До деплоя этой ветки: либо смержить и задеплоить #772, либо **удалить `PROD_LAVA_JWT_TOKEN` и `PROD_LAVA_SHOP_ID`** в Railway. На `main` удаление Lava-env **скроет кнопку WATA**: там она гейтится `lava_service.is_enabled()`.
- [ ] **После** деплоя этой ветки `PROD_LAVA_*` не читаются кодом, их можно удалить без последствий. Проверка:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://<PROD_HOST>/webhooks/lava -d '{}'
# ожидаем 404
```

### 2.2. Прокси подписок удалён

- [ ] `PROD_SUBSCRIPTION_PROXY_ENABLED` должен быть `false` или отсутствовать. Модуль `app/api/subscription_proxy.py` удалён (B5), переменные не читаются. Если флаг был `true` и `sub.atlassecure.ru` смотрит на бота, эти ссылки начнут отдавать 404 — **выяснить до деплоя**.
- [ ] Удалить из Railway (не используются): `PROD_SUBSCRIPTION_PROXY_ENABLED`, `PROD_LEGACY_SAMOPIS_SUB_BASE_URL`, `PROD_XRAY_API_URL`, `PROD_XRAY_API_KEY`, `PROD_VPN_SERVER_URL`. Удалять необязательно: лишние переменные ничего не ломают.

### 2.3. Живые рекуррентные подписки Platega

Код рекуррента удалён. Если Platega спишет деньги по старой подписке, бот **ничего не выдаст**, только пришлёт алерт. Нужно отменить подписки заранее.

```sql
SELECT subscription_id, telegram_id, status, next_charge_at
FROM platega_subscriptions
WHERE status IN ('Active','PendingAgreement','PastDue')
ORDER BY next_charge_at;
```

- [ ] Каждую строку отменить в кабинете Platega (`POST /subscription/{id}/cancel`, см. `docs/providers/platega_api.md`). Пользователю при необходимости выдать остаток вручную.
- [ ] Повторить запрос после отмены: в кабинете Platega статусы должны смениться. Сама таблица обновляется только колбэками, а они теперь только алертят, поэтому **сверять по кабинету**.
- [ ] Можно также проверить до деплоя в боте командой `/platega_sub_status` (есть и на `main`).

### 2.4. WATA

- [ ] `PROD_WATA_ACCESS_TOKEN` задан и не истёк (живёт 1–12 месяцев). Без него кнопки WATA скрыты, reconciler не стартует (`main.py:505-520`).
- [ ] `PROD_WATA_SANDBOX=false`.
- [ ] Публичный ключ подписи — один из двух вариантов:
  - **Вариант А (рекомендуется): закрепить ключ в env.**

    ```bash
    curl -s -H "Authorization: Bearer $PROD_WATA_ACCESS_TOKEN" \
      https://api.wata.pro/api/h2h/public-key | python3 -c 'import sys,json; print(json.load(sys.stdin)["value"])'
    ```

    Значение положить в `PROD_WATA_PUBLIC_KEY_PEM`. Допускается одна строка с литеральными `\n` (`.env.example`). **При ротации ключа в WATA переменную нужно обновить**, иначе webhook будет получать 500 до обновления. WATA повторяет до 32 часов, так что платежи не теряются.
  - **Вариант Б: ленивая загрузка.** Оставить пустым. Ключ грузится при старте (`main.py:516`, лог `WATA_PUBLIC_KEY_WARMUP: ok=True pinned=False`), а при неверной подписи перезапрашивается (не чаще раза в 60 с). Пока ключ не загружен, webhook отвечает 500 (fail-closed).

### 2.5. Бэкап БД

- [ ] Проверить место и размер БД:

```sql
SELECT pg_size_pretty(pg_database_size(current_database())) AS db_size;
SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) AS size
FROM pg_catalog.pg_statio_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 10;
```

  Свободное место на томе Railway Postgres смотреть в UI сервиса (Metrics → Disk). Нужен запас не меньше 2× размера `users` + `subscriptions` + `audit_log`: миграция 081 строит индексы по ним.
- [ ] Снять дамп (формат custom, версия `pg_dump` ≥ версии сервера):

```bash
TS=$(date -u +%Y%m%d_%H%M%S)
pg_dump "$DATABASE_URL" -F c -Z 6 --no-owner --no-acl -f "atcbot_prod_${TS}_pre_audit.dump"
pg_restore --list "atcbot_prod_${TS}_pre_audit.dump" | head -20   # дамп читается
ls -lh "atcbot_prod_${TS}_pre_audit.dump"
```

- [ ] Хранить **вне Railway**: зашифрованное хранилище владельца (`<МЕСТО ХРАНЕНИЯ БЭКАПОВ — заполнить>`), минимум 30 дней. В дампе PII и платёжные данные, в публичные места не класть. Общая стратегия — `docs/runbooks/backup_restore.md`.
- [ ] Восстановление (только при аварии, в **новую** БД):

```bash
pg_restore -d "$RESTORE_DATABASE_URL" --no-owner --no-acl -v atcbot_prod_<TS>_pre_audit.dump
```

### 2.6. Миграции 081 и 082

Миграции применяются **автоматически при старте** (`database/core.py:430` → `migrations.run_migrations_safe`), **до** взятия advisory lock.

Путь апгрейда проверен на копии прод-схемы с данными (`05_prod_readiness.md`, проверка 1):
- применяются ровно 081 и 082;
- 53 существующие таблицы не меняются ни по строкам, ни по содержимому;
- код `main` после этого стартует на той же БД. Каждая идёт в своей транзакции. Если миграция упала, `init_db()` возвращает False и бот работает в деградированном режиме с повтором init в фоне (`main.py:387`).

| Миграция | Что делает | Откуда |
|---|---|---|
| `081_users_list_dashboard_indexes.sql` | 7 индексов `CREATE INDEX IF NOT EXISTS` на `users`, `subscriptions`, `audit_log` | дашборд v2, `04693aab` |
| `082_provisioning_jobs.sql` | таблица `provisioning_jobs` + 2 индекса; `ALTER TABLE platega_subscriptions ADD COLUMN IF NOT EXISTS is_combo` | ядро, T2 |

На `main` последняя миграция — `080_broadcast_trial_key_claims.sql` (`git ls-tree cf8205fc migrations/`).

- [ ] Проверить состояние:

```sql
SELECT version, applied_at FROM schema_migrations ORDER BY version DESC LIMIT 5;
-- ожидаем верхнюю строку '080'; '081' и '082' отсутствуют
SELECT version FROM schema_migrations WHERE version IN ('081','082');   -- 0 строк
SELECT to_regclass('public.provisioning_jobs');                        -- NULL
```

- [ ] Если `081` уже есть (например, на прод когда-то выкатывали `atcnew`), это нормально: раннер пропустит её. Файл `081` в `atcnew` идентичен этой ветке: `git diff refactor/audit-2026-09 atcnew -- migrations/` не показывает `081`. Для контроля: `\di idx_users_created_at`.
- [ ] Если `082` уже есть, **остановиться и разобраться**: 082 нигде, кроме этой ветки, не выкатывалась.
- [ ] **Про дубль 006.** Раннер ключует миграцию **по числовому префиксу** (`migrations.py:67-73`), а не по имени файла. Файлы `006_add_subscription_fields.sql` и `006_broadcast_discounts.sql` оба имеют версию `'006'`. На существующей БД обе давно применены. **Никогда не удалять и не вставлять руками строку `'006'`** в `schema_migrations`, не создавать новые файлы с занятым номером.
- [ ] **Блокировки.** 081 создаёт индексы без `CONCURRENTLY`: на время построения записи в `users`, `subscriptions`, `audit_log` ждут. При текущем объёме это секунды. Если таблицы большие, деплоить в тихое время.
- [ ] **092 (`broadcast_log`) — создать индекс руками ДО деплоя.** Лог рассылок большой (строка на получателя), и без индекса дашборд «Вовлечённость» падает по таймауту. Миграция идёт в транзакции, `CONCURRENTLY` в ней нельзя, а построение обычным `CREATE INDEX` может не уложиться в `command_timeout=30` с (см. ниже) и заблокирует запись в лог. Поэтому заранее:

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_broadcast_log_broadcast_status
    ON broadcast_log (broadcast_id, status);
-- проверка: индекс валиден
SELECT indexrelid::regclass, indisvalid FROM pg_index
 WHERE indexrelid = 'idx_broadcast_log_broadcast_status'::regclass;   -- indisvalid = t
```

  Если `indisvalid = f` (построение прервалось), `DROP INDEX CONCURRENTLY idx_broadcast_log_broadcast_status;` и создать снова. После этого миграция 092 — no-op.
- [ ] **Таймаут.** Миграции идут на соединении пула с `command_timeout=30` с (`database/core.py:250`, env `DB_POOL_COMMAND_TIMEOUT` **без префикса**). Если построение индекса не уложится в 30 с:
  - миграция 081 упадёт;
  - бот уйдёт в деградированный режим;
  - `retry_db_init` будет падать на том же месте.

  Проверить объём:

```sql
SELECT relname, n_live_tup FROM pg_stat_user_tables
WHERE relname IN ('users','subscriptions','audit_log');
```

  Если это миллионы строк, на первый деплой задать `DB_POOL_COMMAND_TIMEOUT=300`, после успешного старта убрать.
- [ ] Опционально — прогнать миграции на копии заранее: восстановить дамп из 2.5 в отдельную БД и запустить stage-сервис на ней (раздел 3.1).

### 2.7. Переменные окружения Railway

Префикс по `APP_ENV`: `PROD_…` для прода. `config.env()` сначала ищет `PROD_X`, для части переменных — и без префикса (см. `config.py`).

**Новые или изменённые:**

| Переменная | Рекомендуемое значение на первом деплое | Комментарий |
|---|---|---|
| `PROD_USE_NEW_PROVISIONING` | `on` (по умолчанию) | `off \| shadow \| on`. **По умолчанию `on`** — новое ядро на всех точках входа. `off` — только аварийный выключатель (старый путь). `shadow` работает как `off`. Невалидное значение → значение по умолчанию (`on`) + warning (`app/services/provisioning_flags.py`). |
| `PROD_NEW_PROVISIONING_ENTRYPOINTS` | *(пусто или не задано)* | Список через запятую из `webhook,telegram,balance,autorenew,admin,gift,grants,trial`. **Пусто = ВСЕ точки при `on`** (`provisioning_flags.py:48-52`). Неизвестные значения игнорируются (warning). `platega_recurring` больше не существует. |
| `PROD_WATA_ACCESS_TOKEN` | действующий токен | см. 2.4 |
| `PROD_WATA_SANDBOX` | `false` | |
| `PROD_WATA_PUBLIC_KEY_PEM` | PEM из 2.4 или пусто | |
| `PROD_BRAND_NAME` | `Atlas Secure` | Все `BRAND_*` необязательны, пусто = значения по умолчанию (`app/branding.py`) |
| `PROD_BRAND_SHORT` | `Atlas` | |
| `PROD_BRAND_LOGO_URL` | *(пусто)* | https-URL или путь того же origin |
| `PROD_BRAND_PRIMARY_COLOR` | `#F2E8C9` | `#RRGGBB` |
| `PROD_BRAND_SUPPORT_URL` | `https://t.me/atlas_suppbot` | |
| `PROD_BRAND_CHANNEL_URL` | `https://t.me/ATC_VPN` | |
| `PROD_BRAND_BOT_USERNAME` | *(пусто)* | пусто = `BOT_USERNAME` (`atlassecure_bot`) |

**Существующие, проверить:**

| Переменная | Проверка |
|---|---|
| `PROD_JWT_SECRET`, `PROD_DASHBOARD_BASE_URL` | Обе заданы, иначе дашборд не монтируется (`config.py:97-99`). **`DASHBOARD_BASE_URL` должен точно совпадать с origin, по которому открывается дашборд** (схема + хост). Иначе после деплоя все мутации получат 403 (CSRF-проверка `Origin`). |
| `PROD_REDIS_URL` | Желательно: FSM, счётчики блокировок логина, кеш Idempotency-Key. Без Redis всё это живёт в памяти процесса и сбрасывается рестартом. |
| `PROD_TRIAL_BYPASS_MB` | Если задана — `500`. `config.py:558` берёт `TRIAL_BYPASS_MB`, по умолчанию 500. `.env.example` уже приведён к `TRIAL_BYPASS_MB`. Старую `PROD_TRIAL_BYPASS_GB`, если есть, код не читает. |
| `PROD_REMNAWAVE_BYPASS_USERNAME_PATTERN` | `{telegram_id}` (так названы ~2500 существующих сущностей). **Не менять.** |
| `PROD_PURCHASE_FLOW_REMNAWAVE` | `true` |
| `PROD_PLATEGA_MERCHANT_ID`, `PROD_PLATEGA_SECRET`, `PROD_CRYPTOBOT_API_TOKEN` | без изменений |

**Новых обязательных переменных нет, дефолты существующих не менялись.** Проверено AST-сканом чтений env в `main` и в ветке (`05_prod_readiness.md`, проверка 2).

**Удалить (после деплоя; кодом не читаются):**
- `PROD_LAVA_*`;
- `PROD_SUBSCRIPTION_PROXY_ENABLED`, `PROD_LEGACY_SAMOPIS_SUB_BASE_URL`;
- `PROD_XRAY_API_URL`, `PROD_XRAY_API_KEY`, `PROD_XRAY_API_TIMEOUT`, `PROD_XRAY_SYNC_ENABLED`;
- `PROD_VPN_SERVER_URL`, `PROD_VPN_PROVISIONING_ENABLED`, `PROD_SUB_BASE_URL`;
- `PROD_SITE_API_URL`, `PROD_SITE_BOT_API_KEY` (синхронизация с сайтом удалена);
- `MIGRATION_LOG_DIR`.

### 2.8. Дашборд: доступ админа

- [ ] Проверить, настроен ли вход по паролю или passkey:

```sql
SELECT count(*) FROM admin_credentials;
SELECT count(*) FROM admin_passkeys;
```

- [ ] Если обе таблицы пусты, после деплоя войти по свежей ссылке `/admin` (действует 15 минут) и сразу установить пароль. Если пароль есть, но забыт — «🔄 Сбросить пароль» в боте.

### 2.9. CI

- [ ] На ветке или PR зелёный агрегат `CI OK` — `.github/workflows/ci.yml`, см. `docs/ci.md`. В него входят:
  - `Lint & Syntax`, `Tests`, `Frontend Build`, `Docker Build`;
  - `Migration Integrity`: реальный `database.init_db()` на пустом `postgres:16`, затем повторный boot;
  - `Security Scan`.

  Деплой прода ждёт успешного CI по push в `main` (`deploy.yml`, job `ci-gate`).
- [ ] На 2026-09-13 ветки **нет на `origin`**, CI по ней ни разу не запускался. Локально прошли ruff, compileall, pytest и Migration Integrity, но Docker и Frontend не собирались.

---

## 3. Деплой

### 3.1. Вариант 1 (рекомендуется): сначала stage-сервис

1. В Railway создать отдельный сервис или окружение с `APP_ENV=stage` и **копией** прод-БД (восстановить дамп из 2.5). Отдельный тестовый бот-токен: `STAGE_BOT_TOKEN`, `STAGE_WEBHOOK_URL`. `STAGE_WATA_SANDBOX=true`, отдельный Redis.
2. Задеплоить ветку `refactor/audit-2026-09` в этот сервис. Railway собирает `Dockerfile`.
3. Пройти раздел 3.3 и сценарии 3.4. Опционально включить `STAGE_USE_NEW_PROVISIONING=on` и прогнать сценарии на копии.

> ⚠️ Stage на копии прод-БД работает с **прод-панелью Remnawave**, если `STAGE_REMNAWAVE_*` указывают на неё: воркеры (автопродление, активация, provisioning) начнут менять реальных пользователей. Для stage — отдельная панель или пустые `STAGE_REMNAWAVE_*`.
- При пустых `STAGE_REMNAWAVE_URL`/`TOKEN` код ставит `REMNAWAVE_ENABLED=false` и `VPN_ENABLED=false` (`config.py`), и `traffic_monitor` не стартует.
- Остальные воркеры стартуют, но выдачу в панель не делают.
- Какая панель у stage сейчас, решает владелец. По умолчанию оставлять `STAGE_REMNAWAVE_*` пустыми.

### 3.2. Вариант 2: прод через PR в `main`

1. Открыть PR `refactor/audit-2026-09` → `main`: `gh pr create --base main --head refactor/audit-2026-09`. **Мержит владелец.**
2. Push в `main` запускает `.github/workflows/deploy.yml`: ждёт CI → POST на `PRODUCTION_DEPLOY_HOOK_URL` → через 30 с проверяет `PRODUCTION_HEALTH_URL` (5 попыток по 10 с). Если секреты не заданы, шаги пропускаются. Тогда деплой делает автодеплой Railway из GitHub (если включён) или ручной Redeploy в UI.
3. Перед мержем: все переменные из 2.7 уже выставлены, `USE_NEW_PROVISIONING=off`.

### 3.3. Что происходит при старте (`main.py`)

1. `init_db()` (`main.py:186`): проверка связи → пул → **миграции 081, 082** → пересоздание пула → legacy inline-DDL → `DB_READY=True`. Логи: `Applying migration 081…`, `Migration 082 applied successfully`, `Database migrations applied successfully`, `DB_POOL_RECREATED_AFTER_MIGRATIONS`.
2. Если БД недоступна или миграция упала, бот стартует в деградированном режиме. `retry_db_init()` повторяет init каждые 30 с и после восстановления вызывает ту же `start_db_services()`, что и обычный старт: advisory lock первым, затем полный набор воркеров, каждый не более одного раза.
3. Воркеры как `asyncio.create_task` — один список в `start_db_services()` (`main.py`): reminders, trial_notifications, farm_notifications, traffic_monitor (при `REMNAWAVE_ENABLED`), fast_expiry_cleanup, auto_renewal (по флагам), activation_worker, wata_reconciler, provisioning_worker.
4. **WATA** (только при `WATA_ACCESS_TOKEN`): `wata_reconciler` (раз в 5 мин) + прогрев ключа, лог `WATA_PUBLIC_KEY_WARMUP: ok=… pinned=…` (`start_db_services`).
5. **Provisioning worker стартует всегда**, независимо от флага, нужен только `DB_READY` (`start_db_services`). Лог: `Provisioning worker task started (interval=15.0s)` и `PROVISIONING_WORKER started (interval=15.0s, max_jobs=50, job_timeout=45.0s, lease=120s)`. При флаге `off` очередь пуста, воркер молчит: tick-лог пишется только при работе.
6. Дашборд: `DASHBOARD mounted: api+ws+static (dist=…)` (`app/api/__init__.py:94`).
7. **Перекрытие деплоев.**
   - Advisory lock берётся **после** миграций и **до** любого воркера (`acquire_instance_lock()` в `start_db_services()`, `main.py`).
   - Если старый инстанс ещё жив, новый в PROD пишет `Advisory lock not acquired in PROD — another instance may be running` и выходит с кодом 1. Railway его перезапускает.
   - Это ожидаемо, пока старый инстанс не остановлен. Поведение как на `main`.
8. **Остановка по SIGTERM (redeploy)** — процесс завершается сразу, блок `finally` в `main()` не выполняется: нет `WEBHOOK_DELETED`, `Advisory lock released`. Проверено локально, поведение как на `main`. Это безопасно:
   - lock снимается вместе с соединением;
   - `running`-задачи `provisioning_jobs` забираются повторно через lease 120 с;
   - апдейты, пришедшие, пока процесс не отвечал, Telegram хранит до 24 ч и отдаёт после `set_webhook` нового инстанса: `drop_pending_updates=False` (`main.py`). До 2026-09-14 стояло `True`, и такие апдейты, включая `successful_payment` (деньги уже списаны), выбрасывались: доступ не выдавался, алерта не было.

### 3.4. Health-check

```bash
curl -s https://<PROD_HOST>/health | python3 -m json.tool
```

Ожидаем HTTP 200 (`app/api/__init__.py:101-193`):

```json
{
  "database": "connected",
  "redis": "connected",
  "payment_providers": {"platega": "enabled", "cryptobot": "enabled", "vpn_api": "enabled|disabled"},
  "status": "ok"
}
```

- 503 `{"status":"degraded","database":"not_ready"}` — `init_db` не прошёл (смотреть логи миграций).
- 503 с `"redis":"unavailable"` — Redis задан, но недоступен.
- `redis` отсутствует — Redis не настроен (допустимо).
- WATA в `/health` не показывается. Её статус — по логам `Wata reconciler task started` и `WATA_PUBLIC_KEY_WARMUP`.

Проверка схемы после старта:

```sql
SELECT version, applied_at FROM schema_migrations WHERE version IN ('081','082');   -- 2 строки
SELECT status, count(*) FROM provisioning_jobs GROUP BY 1;                          -- 0 строк при off
SELECT column_name FROM information_schema.columns
 WHERE table_name='platega_subscriptions' AND column_name='is_combo';               -- 1 строка
```

### 3.5. Smoke-проверки вручную (флаг `off`)

**Бот** (тестовый аккаунт, не админ):
- [ ] `/start` → главное меню, без ошибок.
- [ ] Экран покупки: способы оплаты. Нет «Карта (Lava)» и «Подписка СБП», WATA видна (если настроена).
- [ ] **Тестовая покупка минимальной суммы** (basic 30 дней или минимальный пакет ГБ) реальной оплатой через Platega или WATA: оплата → сообщение об успехе → доступ в профиле. SQL:

```sql
SELECT purchase_id, status, payment_provider, tariff, price_kopecks, created_at
FROM pending_purchases WHERE telegram_id = <TG_ID> ORDER BY created_at DESC LIMIT 3;
SELECT * FROM payment_errors WHERE telegram_id = <TG_ID> ORDER BY created_at DESC LIMIT 5;  -- пусто
```

- [ ] Баланс: пополнение на минимальную сумму → зачислена ровно сумма счёта. Покупка с баланса → одно списание (быстрый двойной тап — всё равно одно).
- [ ] Подарок: создать или активировать подарочный код → получатель получает доступ.
- [ ] Магазин (`mini_shop`) открывается, товары и цены прежние. **Покупок в магазине не делать без необходимости.**
- [ ] Триал на новом аккаунте → доступ, 500 МБ bypass.

**Дашборд:**
- [ ] `https://<DASHBOARD_BASE_URL>/dashboard/` → страница входа. Вход по паролю и по passkey.
- [ ] Старая 30-дневная ссылка → не пускает (ожидаемо).
- [ ] Страницы **Обзор**, **Деньги**, **Панель**, **Операции** грузятся без ошибок. «Операции» → «Очередь выдачи» показывает таблицу (`available=true`, 0 задач).
- [ ] Любое изменение (например, скидка тестовому пользователю) проходит, не 403. 403 — проверить `DASHBOARD_BASE_URL`.
- [ ] Брендинг: `curl -s https://<PROD_HOST>/dashboard/api/branding` → имя «Atlas Secure».

**Логи** в первые 15 минут: нет `CRITICAL`, `Migration … FAILED`, `PROVISIONING_WORKER_TICK_ERROR`, `WATA_PUBLIC_KEY_WARMUP: failed`.

---

## 4. Поэтапное включение платёжного ядра

**Принцип.** Флаг читается на каждом вызове (`provisioning_flags.py`, `config.env()`), поэтому новое значение вступает в силу без изменения кода, как только процесс его увидит. В Railway изменение переменной применяется новым деплоем (кнопка Deploy после правки Variables). Это рестарт процесса: воркер при остановке отправляет буфер алертов, `running`-задачи забираются повторно после истечения lease (120 с). **Shadow-режим (T17) не делаем**: поведение зафиксировано характеризующими тестами (T0 и тесты T8–T16).

**Всегда перечислять точки явно.** Пустой `NEW_PROVISIONING_ENTRYPOINTS` при `on` включает **всё сразу**.

### 4.0. Общие SQL мониторинга (для каждого шага)

```sql
-- M1. Состояние очереди
SELECT status, count(*) FROM provisioning_jobs GROUP BY 1 ORDER BY 1;

-- M2. Разбивка по источнику за последний час
SELECT source, status, count(*)
FROM provisioning_jobs
WHERE created_at > (now() AT TIME ZONE 'UTC') - interval '1 hour'
GROUP BY 1,2 ORDER BY 1,2;

-- M3. Застрявшие: открытые дольше 10 минут
SELECT id, idempotency_key, telegram_id, source, status, attempts,
       created_at, next_attempt_at, lease_until, left(last_error, 200) AS last_error
FROM provisioning_jobs
WHERE status IN ('pending','running')
  AND created_at < (now() AT TIME ZONE 'UTC') - interval '10 minutes'
ORDER BY created_at;

-- M4. Мёртвые
SELECT id, idempotency_key, telegram_id, source, tariff_key, attempts,
       bypass_add_bytes, bypass_base_bytes, bypass_target_bytes, premium_until,
       updated_at, last_error
FROM provisioning_jobs WHERE status = 'dead' ORDER BY updated_at DESC LIMIT 50;

-- M5. Ошибки платежей за час по стадиям
SELECT stage, payment_provider, count(*)
FROM payment_errors
WHERE created_at > now() - interval '1 hour'
GROUP BY 1,2 ORDER BY 3 DESC;

-- M6. Время выдачи (от создания задачи до done) за час
SELECT source, count(*),
       percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM done_at - created_at)) AS p50_s,
       max(extract(epoch FROM done_at - created_at)) AS max_s
FROM provisioning_jobs
WHERE status='done' AND done_at > (now() AT TIME ZONE 'UTC') - interval '1 hour'
GROUP BY 1;
```

Значения `source` соответствуют точке входа; ключи идемпотентности — `purchase:{id}`, `balance:{payment_id}`, `autorenew:{payment_id}`, `admin:{uuid}`, `gift:{code}`, `trial:{tg}`, `game:…`, `promo:…`, `bonus:…`, `bgift:…`, `btk:…` (`02_payment_core_plan.md` §A, T13, T16).

**Как работает очередь:**
- Воркер раз в 15 с берёт до 50 задач. Бэкофф `min(30 с · 2^n, 1 ч)`.
- Через 24 часа без успеха задача переходит в `dead` (`provisioning.py:64`, `DEAD_AFTER`). `ProvisioningPermanent` → `dead` сразу.
- Порядок по пользователю строгий (FIFO + lease 120 с).
- `running` с истёкшим lease забирается повторно — это нормально после рестарта.

**Алерты** (`provisioning.py:532-610`, `700-960`):
- Первая неудача задачи — forced-алерт `Provisioning job RETRY at …`. Повторные — с кулдауном.
- `dead` — forced `Provisioning job DEAD (permanent error | 24h without success)`.
- `conflict` (лимит bypass изменён вне задачи) — `dead` + алерт с отдельной SQL-подсказкой.
- При лавине: не больше 5 алертов на вид за 5 минут, остальное приходит **одной сводкой** на вид за окно (`ALERT_WINDOW_S=300`, `ALERT_IMMEDIATE_PER_WINDOW=5`).
- Все неудачи дублируются в `payment_errors` со `stage='provisioning'`.

**Ручной повтор** (SQL есть прямо в тексте алерта):

```sql
-- обычный повтор одной задачи
UPDATE provisioning_jobs SET status='pending', next_attempt_at=now() AT TIME ZONE 'UTC' WHERE id=<JOB_ID>;

-- conflict: ТОЛЬКО после проверки в панели, что ГБ по этой задаче НЕ начислены
UPDATE provisioning_jobs SET status='pending', bypass_base_bytes=NULL, bypass_target_bytes=NULL,
       next_attempt_at=now() AT TIME ZONE 'UTC' WHERE id=<JOB_ID>;
```

Повтор идемпотентен (`idempotency_key UNIQUE`, CAS по bypass, premium не сокращается). Второго +N ГБ не будет, если план CAS не сброшен вручную.

**Общий критерий «идём дальше»** (через 30–60 минут и не меньше ~5 реальных событий этой точки):
1. M1: `dead = 0`. `pending/running` только свежие (M3 пусто).
2. M5: нет новых `stage='provisioning'`, и прочие стадии не выросли относительно фона до включения.
3. Выборочно 2–3 пользователя: ГБ в панели = ожидаемые по каталогу, срок premium верный.
4. Нет жалоб в поддержку «оплатил, доступа нет».

**Общий откат шага:** убрать точку из `PROD_NEW_PROVISIONING_ENTRYPOINTS` (или `PROD_USE_NEW_PROVISIONING=off` для всего). Новые события пойдут старым путём. **Открытые задачи воркер доделает** — он работает всегда. `dead` повторить вручную SQL выше или решить вручную.

### Шаг 1 — `webhook` (Platega, WATA, CryptoBot, WATA-reconciler; T8)

```
PROD_USE_NEW_PROVISIONING=on
PROD_NEW_PROVISIONING_ENTRYPOINTS=webhook
```

- **Что затронуто:** `finalize_purchase` для внешних провайдеров (`database/subscriptions.py:4507-4528`, `provisioning_entrypoint(provider)` = `webhook` для всего, кроме Telegram/Stars). Пакеты ГБ через вебхук тоже идут через outbox (лог `TRAFFIC_PACK_VIA_OUTBOX`, `confirmation.py:1174`). Магазин не затронут.
- **Проверить:** M1/M2 — появляются `purchase:…` с `status='done'`. M6 — p50 в пределах секунд (быстрый путь `run_now` после commit). Сообщения пользователям приходят. Нет роста 500 у вебхуков.
- **Ожидаемые алерты:** при недоступной панели — `RETRY` по первым задачам (webhook при этом отвечает 200, выдача в очереди, пользователь видит «активация в процессе»).
- **Нюанс:** replay вебхука по покупке, финализированной до включения флага, идёт старым resync (T8).
- **Откат:** `PROD_USE_NEW_PROVISIONING=off`. Не очищать `PROD_NEW_PROVISIONING_ENTRYPOINTS` при `on`: пустой список включит все точки.

### Шаг 2 — `telegram` (Telegram-native и Stars; T9)

```
PROD_NEW_PROVISIONING_ENTRYPOINTS=webhook,telegram
```

- `webhook,telegram` можно включать вместе, это проверено тестом (T9). Для осторожности — отдельным шагом.
- **Проверить:** покупка картой в Telegram и Stars. basic даёт +10 (было 20), combo 30 дней +75 (было 150). `purchase:…` задачи `done`.
- **Откат:** вернуть `webhook`.

### Шаг 3 — `balance` (T10)

```
PROD_NEW_PROVISIONING_ENTRYPOINTS=webhook,telegram,balance
```

- **Проверить:** покупка с баланса. basic +10 (было 20), combo новый +75 (было 95), продление combo +75 (было 85). `balance:{payment_id}`. При недоступной панели покупка **проходит** (раньше падала с «vpn_key is missing»), выдача в очереди.
- Сверка денег: списание одно на покупку.

```sql
SELECT telegram_id, count(*) FROM payments
WHERE created_at > now() - interval '1 hour' GROUP BY 1 HAVING count(*) > 1;
```

### Шаг 4 — `autorenew` (T12)

```
PROD_NEW_PROVISIONING_ENTRYPOINTS=webhook,telegram,balance,autorenew
```

- **Внимание, меняется сумма списания:** combo продлевается по цене combo (329 ₽ за 30 дней) с ГБ combo. basic и plus — прежняя цена, +10 ГБ. Легаси `biz_*` — как Plus по ГБ, **цена 199 ₽** (G0). Предупредить поддержку.
- **Проверить:** ближайший цикл `auto_renewal` (задачи `autorenew:…`). Plus остаётся plus, уведомление показывает реальную сумму, а не 0.00 ₽.

```sql
SELECT id, telegram_id, tariff_key, bypass_add_bytes/1024^3 AS gb, premium_until, status
FROM provisioning_jobs WHERE source='autorenew' ORDER BY id DESC LIMIT 20;
```

- Идти дальше — после минимум одного прохода автопродления с реальными продлениями (может занять сутки).

### Шаг 5 — `admin,gift` (T13, T14)

```
PROD_NEW_PROVISIONING_ENTRYPOINTS=webhook,telegram,balance,autorenew,admin,gift
```

- **admin:** выдача днями и минутами из бота и дашборда даёт **только premium, 0 ГБ**. Предупредить админов. Двойной «Подтвердить» даёт одну выдачу. Ключ `admin:{uuid4}`.
- **gift:** активация подарка basic 90d → +10 ГБ и продление premium. Ключ `gift:{code}`.
- **Проверить:** одна тестовая выдача админом на свой аккаунт, одна активация подарка.

### Шаг 6 — `grants,trial` (T16, T15)

```
PROD_NEW_PROVISIONING_ENTRYPOINTS=webhook,telegram,balance,autorenew,admin,gift,grants,trial
```

- **grants:** игра, промо-ссылки, бонус, bypass-gift, ключ из рассылки (+1 день и +1 ГБ). Выдача днями — 0 ГБ. ГБ-награды — ровно +N.
- **trial:** `activate_trial` (T15) — 500 МБ bypass + 3 дня, ключ `trial:{tg}`. Раньше вызов несуществующей функции молча не выдавал триал в 3 местах.
- **Риск:** массовый бонус при лежащей панели даёт много задач. Алерты агрегируются в сводку (`7b11ca83`).
- **Финал:** полный список эквивалентен пустому, но **явный список безопаснее**: оставить его.

### После шага 6

- 1–2 недели в `on` → T18 (удаление старых путей) отдельным PR.

---

## 5. Мониторинг первых 24 часов

### 5.1. Логи (Railway → сервис → Logs, фильтр по тегу)

| Тег | Где | Значение / действие |
|---|---|---|
| `PROVISIONING_WORKER started` | `provisioning_worker.py:60` | воркер жив (один раз при старте) |
| `PROVISIONING_WORKER_TICK processed=… done=… retried=… dead=… errors=…` | `:123` | INFO только когда что-то обработано. WARNING при `dead` или `errors` → M4 |
| `PROVISIONING_WORKER_TICK_ERROR`, `…_CLAIM_FAILED`, `…_JOB_TIMEOUT`, `…_JOB_ERROR` | `:71,105,149,157` | сбой цикла или БД → проверить БД, M3 |
| `PROVISIONING_RETRY` / `PROVISIONING_DEAD` | `provisioning.py:641,634` | повтор / мёртвая задача → M4, ручной повтор |
| `PROVISIONING_ALERT_FAILED`, `…_NO_BOT`, `…_BUFFER_FAILED`, `PROVISIONING_ALERT_DIGEST_*`, `PROVISIONING_ALERTS_UNSENT_ON_SHUTDOWN` | `provisioning.py:651-880`, `provisioning_worker.py:89` | алерт не ушёл в Telegram. Всё есть в `payment_errors` (stage `provisioning`) |
| `WATA_PUBLIC_KEY_WARMUP: ok=… pinned=…` | `wata_service.py:443-449` | `failed` или `key unavailable` → webhook WATA отвечает 500, см. 2.4 |
| `WATA_WEBHOOK_REJECTED: reason=…` | `wata_service.py:797` | отказ по сумме, валюте или подписи. Если массово — проверить ключ |
| `WEBHOOK_RETRY_REQUESTED` | `confirmation.py:270` | вебхуку отдан 500 (транзиентная ошибка), провайдер повторит |
| `PLATEGA_RECURRING_DISABLED_CALLBACK` | `platega_service.py:564` | пришёл колбэк старой подписки → **отменить её в кабинете Platega**, при списании выдать или вернуть вручную |
| `BALANCE_TOPUP_OVERPAYMENT` | `database/subscriptions.py:5125` | переплата не зачислена (норма при комиссии провайдера) |
| `TRAFFIC_PACK_VIA_OUTBOX` | `confirmation.py:1174` | пакет ГБ через outbox (при `webhook`/`telegram`) |
| `DASHBOARD_AUTH_LOCKED scope=…`, `DASHBOARD_AUTH_LOGIN_FAIL` | `security.py:218`, `auth.py:225` | блокировка или неудачный вход; частые с чужих IP — подбор пароля |
| `LAVA_WEBHOOK_REJECTED` | только в hotfix #772 | **в этой ветке не встречается**: Lava удалена, `/webhooks/lava` → 404 |
| `CRITICAL`, `Migration … FAILED` | везде | немедленно смотреть |

### 5.2. Дашборд

- **Операции** (`/dashboard/operations`, API `/metrics/operations?hours=24`):
  - очередь выдачи по статусам и самая старая открытая задача;
  - ошибки платежей по стадиям и провайдерам;
  - висящие счета и активации.
- **Обзор**: KPI и алерты (`build_alerts`). **Деньги**: выручка за окно против предыдущего (резкий провал → проверить провайдеров).

### 5.3. SQL-срез раз в 1–2 часа

M1, M3, M4, M5 из 4.0, плюс:

```sql
-- стадии ошибок за 24 ч
SELECT stage, count(*) FROM payment_errors
WHERE created_at > now() - interval '24 hours' GROUP BY 1 ORDER BY 2 DESC;
-- ожидаемые stage: provisioning, webhook_purchase_not_found, webhook_provider_mismatch,
-- platega_recurring_disabled и прежние стадии провайдеров

-- висящие активации
SELECT count(*) FROM subscriptions WHERE activation_status='pending';
```

---

## 6. Откат

### 6.1. Частичный — по точке входа (предпочтительно)

1. Убрать точку из `PROD_NEW_PROVISIONING_ENTRYPOINTS` (или `PROD_USE_NEW_PROVISIONING=off` для всех).
2. Railway перезапустит сервис с новой переменной.
3. Открытые задачи (`pending`/`running`) воркер доделает: он работает при любом флаге. Следить по M1 и M3 до нуля открытых.
4. `dead` — ручной повтор (4.0) или ручная выдача.
5. `activation_worker` не трогает пользователей с открытой задачей (`app/services/activation/service.py:200-232`), двойной выдачи не будет.

### 6.2. Полный — вернуть предыдущий образ (`main` = `cf8205fc`)

1. **Railway → сервис → Deployments → предыдущий успешный деплой → Redeploy** (или revert-коммит в `main` через PR владельца).
2. Сначала выставить `PROD_USE_NEW_PROVISIONING=off`, дождаться M1 без открытых задач. Старый код **не знает** про `provisioning_jobs` и не доделает задачи из очереди.
3. Если откат срочный и в очереди остались `pending`/`running`/`dead`, выгрузить их и выдать вручную после отката:

```sql
\copy (SELECT * FROM provisioning_jobs WHERE status IN ('pending','running','dead')) TO 'open_jobs.csv' CSV HEADER
```

4. **Схему не откатывать.** Миграции 081 и 082 additive, старый код их игнорирует (`082_provisioning_jobs.sql`, комментарий «Rollback»). Не удалять строки `081`/`082` из `schema_migrations`.
5. **После полного отката возвращаются уязвимость Lava и прежнее поведение:** fail-open подпись WATA, 200 на транзиентные ошибки, двойное списание с баланса, кнопки Lava и рекуррента. Если на `main` не смержен #772, **сразу удалить `PROD_LAVA_*`** (кнопка WATA при этом скроется) или смержить #772.
6. Env `PROD_LAVA_*` / `PROD_SUBSCRIPTION_PROXY_ENABLED`, удалённые после деплоя, при откате **не возвращать** (кроме случая, если прокси реально использовался — 2.2).

### 6.3. Что НЕ откатывается

- Данные `provisioning_jobs` и колонка `platega_subscriptions.is_combo` остаются (DROP — только отдельным релизом).
- **ГБ и дни, уже выданные через outbox,** остаются у пользователей. Списания combo по цене combo (автопродление) не возвращаются автоматически.
- **Авторизация дашборда:** пароли и passkey, установленные через новую форму, остаются в `admin_credentials`/`admin_passkeys`.
  - **Magic-ссылки.** 30-дневные ссылки, выпущенные до деплоя, после отката на старый код снова станут валидны, если не истекли. На `cf8205fc` такой JWT даёт полный Bearer-доступ к API (`app/api/dashboard/deps.py`, `verify_token`: проверяются только подпись и `exp`).
  - **Как погасить ссылки:** сменить `PROD_JWT_SECRET`.
  - **Cookie-сессии** (`atlas_admin_session`, 5 дней) от `JWT_SECRET` **не зависят**: это случайные токены в Redis под ключом `dashboard:session:<token>` либо в памяти процесса. Одинаково на `main` и в ветке.
  - **Как погасить сессии:** удалить ключи `dashboard:session:*` в Redis (или рестарт, если Redis нет).
- Отменённые рекуррентные подписки Platega не восстанавливаются: рекуррент при откате заработает только для ещё активных.
- Записи в `payment_errors`.

---

## 7. Известные ограничения и открытые вопросы

1. **Остаточные риски T4** (`02_payment_core_plan.md`, «Итоги T4»):
   - Если читатель premium не увидел существующую сущность, а её `expireAt` дальше цели, create с адопцией **сократит срок**. Постфактум не обнаруживается. Вероятность низкая.
   - Legacy-писатели лимита bypass (экраны «бесплатные 10 ГБ»: `traffic.py:312`, `screens.py:596`, `admin/base.py:890`) между двумя чтениями CAS дают `conflict` → `dead` + алерт. Это ожидаемо, уходит с T18.
   - Кэш premium-URL обновляется только при смене uuid/id.
2. **Баги старого пути, исправленные только под флагом.** Пока точка выключена, остаются:
   - лишние ГБ: баланс basic 20 и combo 95/85, Telegram basic 20 и combo 150, подарок 20, админ 20/10;
   - автопродление plus → basic и «0.00 ₽»;
   - combo автопродление за цену basic с +10 ГБ;
   - баланс при лежащей панели падает;
   - триал не выдаётся из 3 мест (`activate_trial` отсутствовал на старом пути);
   - двойной «Подтвердить» админ-выдачи в боте.
3. **Shadow-режим (T17) не реализован.** `shadow` = `off`.
4. **Курс Stars и комиссии провайдеров не утверждены** (`metrics.md`, «Решения владельца» п.5). Выручка показывается «до комиссий», Stars отдельной строкой. Баг учёта Stars за bypass-пакет из recon (P0-F, `price_kopecks=price_stars`) в этой ветке не воспроизводится: пути покупки пакета ГБ за Stars нет. В `app/handlers/traffic.py` осталось только упоминание в docstring, хэндлер `bypass_pay_stars:` удалён как мёртвый (`e4f038c3`).
5. **Двойная отправка в дашборде:** закрыта `Idempotency-Key` (`4bb424c5`) для выдач, баланса, рассылок, промо и сверок. Без Redis кеш ключей в памяти процесса (10 минут, сбрасывается рестартом). Запросы без заголовка (curl, скрипты) не защищены.
6. **Магазин не тронут** (`SCOPE.md`). Ветка магазина в `confirmation.py` не сверяет сумму (только warning) — находка recon §3a, решение за владельцем. Единственные касания: удаление кнопки Lava и фикс Spotify через Telegram (`404cd66e`).
7. **Слой экранов бота** переписывается позже по одному экрану (`SCOPE.md` §Цель). Бесплатные 10 ГБ при открытии экранов (`02_payment_core_plan.md`, факт 4) не тронуты.
8. **Уведомления:** P0-фиксы из `docs/notifications/bugs-and-risks.md` (N-01 … N-07) **влиты в эту ветку** (`2cba0b11`, тесты `tests/services/test_notifications_p0.py`). N-06 «0.00 ₽» исправлен и в старом пути, и в outbox — флаг `autorenew` для него не нужен.
9. **Рекуррент Platega:** таблицы `platega_subscriptions`/`…_charges` остаются. DROP — отдельным релизом.
10. **Легаси `biz_*`:** цена автопродления 199 ₽ при ГБ Plus — допущение, владелец может поднять до цены Plus.
11. **Пользователи, уже получившие лишние ГБ,** остаются как есть (G5).
12. **`TRIAL_BYPASS_GB`** — исправлено: `.env.example` теперь задаёт `TRIAL_BYPASS_MB`, код читает её же (`config.py:558`).
13. **Нет `railway.json`/`railway.toml`:** healthcheck-путь и автодеплой настроены в UI Railway, кодом это не проверить. Владельцу: в UI сервиса убедиться, что healthcheck = `/health`, и включён «Wait for CI» или автодеплой выключен (`docs/ci.md`).
14. **Сырой ключ `errors.database_unavailable`** (нет ни в `ru`, ни в `en`) показывается в 11 местах игры (`app/handlers/game.py`), только при недоступной БД. Было и на `main`.
15. **Premium с `expireAt` на 10 лет вперёд (2026-09-14).** У части строк bypass-колонки `subscriptions.remnawave_id` / `remnawave_uuid` указывали на premium-сущность (`tg_{id}_premium`), и старые bypass-хелперы (`remnawave_service.extend_remnawave_for_bypass`, `disable_remnawave_user`, `renew_remnawave_user`) продлевали premium на +10 лет или отключали его. Исправлено: bypass ищется по `username == str(telegram_id)`, кеш перезаписывается сам (лог `REMNAWAVE_BYPASS_CACHE_HEALED`), а `remnawave_api` не отправляет premium `expireAt` дальше 5 лет (лог `REMNAWAVE_PREMIUM_FAR_EXPIRE_BLOCKED` и алерт `vpn_api`). Уже выданные +10 лет код **не откатывает**: это решение владельца.
    Сколько строк заражено (только чтение):
    ```sql
    SELECT count(*) FILTER (WHERE remnawave_id = remnawave_premium_id)       AS id_is_premium,
           count(*) FILTER (WHERE remnawave_uuid = remnawave_premium_uuid)   AS uuid_is_premium,
           count(*) FILTER (WHERE remnawave_id = remnawave_premium_id
                               OR remnawave_uuid = remnawave_premium_uuid)   AS contaminated_rows
      FROM subscriptions;
    ```
    Список для разбора (сравнить с панелью: у premium `expireAt` > сейчас + 5 лет):
    ```sql
    SELECT telegram_id, status, is_bypass_only, expires_at,
           remnawave_id, remnawave_premium_id, remnawave_uuid, remnawave_premium_uuid
      FROM subscriptions
     WHERE remnawave_id = remnawave_premium_id OR remnawave_uuid = remnawave_premium_uuid
     ORDER BY telegram_id;
    ```
    Строки лечатся сами при следующем обращении бота к bypass (экран подключения, трафик, продление, истечение). Число должно убывать; `NULL` в колонках под условие не попадает.

---

## 8. Контакты и владельцы решений

| Роль | Кто | Как связаться |
|---|---|---|
| Владелец продукта, мерж в `main`, решения по ценам и магазину | `<ИМЯ>` | `<TG / телефон>` |
| Дежурный на деплое (выполняет этот runbook) | `<ИМЯ>` | `<TG>` |
| Доступ к Railway (env, redeploy, БД) | `<ИМЯ>` | `<TG>` |
| Кабинет Platega (отмена подписок, возвраты) | `<ИМЯ>` | `<TG>` |
| Кабинет WATA (токен, ключ, возвраты) | `<ИМЯ>` | `<TG>` |
| Панель Remnawave | `<ИМЯ>` | `<TG>` |
| Поддержка пользователей (предупредить о combo-цене и 0 ГБ за выдачи днями) | `<ИМЯ>` | `https://t.me/atlas_suppbot` |
| Куда приходят алерты бота | `ADMIN_TELEGRAM_ID` = `<ID>` | — |

**Решение о каждом шаге раздела 4 принимает `<ИМЯ>`.** Фиксировать время включения и значение env в `<журнал/чат>`.

---

## 9. Починка premium `expireAt` > 5 лет (`scripts/fix_premium_over_issuance.py`)

У части premium-сущностей `tg_<id>_premium` в панели `expireAt` стоит примерно на 10 лет вперёд (старые баги:
повтор покупки только трафика, продление от +10 лет bypass-only строки, legacy-хелперы bypass, патчившие premium).
Скрипт ставит дату по реальным покупкам. Код: `app/services/premium_repair.py`, правило —
`database/reconciliation.py::compute_repair_target`. Кнопка «Исправить» в дашборде («Сверка») работает по тому же правилу.

**Из дашборда (без терминала):** «Ещё» → «Настройки» → «Премиум больше 5 лет» — тот же код
(`app/services/premium_repair_job.py` вокруг `premium_repair`). «Проверить» = запуск скрипта без `--apply`: фоновая
проверка, сводка (сколько premium старше 5 лет, откуда дата, сколько на «завтра», сколько дат в БД подрезать), таблица
пользователей и «Скачать CSV» (колонки те же, что у скрипта). «Исправить» = `--apply`: подтверждение со счётчиками,
по желанию «первые N» для пробы (после них задача встаёт на паузу). Пауза / Продолжить / Стоп; прогресс хранится в
`app_settings` (`premium_repair_job`), после перезапуска бота задача показывается «Прервано», «Продолжить» заново
сканирует панель (исправленные в выборку уже не попадают). Одна задача за раз; по завершении — один алерт админу;
каждое действие — в audit_log (`premium_repair_*`). Скрипт с `--apply` при незавершённой задаче в дашборде откажет
(код 3), обойти — `--force`.

**Правило даты:**
- по покупкам: одобренные платежи подписки (`basic_*`, `plus_*`, `combo_*`; не трафик, не подарки, не пополнения)
  прогоняются как продления в боте. Платёж при действующем окне продлевает его, после перерыва открывает новое от
  даты оплаты, срок считается календарными месяцами. Сверху добавляются `admin_grant_days`. Купил на год — ровно год;
- по БД: `subscriptions.expires_at`, только если строка `status='active'`, не bypass-only и дата меньше
  «сейчас + 5 лет». Это покрывает оплату с баланса, подарки и дни из игры и промо, которых нет в `payments`;
- итог: большая из двух. Если даты нет или она в прошлом, ставится «сейчас + 1 день», потому что прошлую дату
  панель не примет (400);
- дата никогда не позже текущей в панели: иначе пользователь пропускается (`would_extend`).

**Запуск на Railway:**
1. Проверка без изменений: `railway run python -m scripts.fix_premium_over_issuance`. Выводит сводку:
   сколько сущностей в панели, сколько premium старше 5 лет, откуда взята дата, причины фолбэка, сколько уйдёт
   на +1 день, сколько дат в БД будет подрезано. Пишет CSV `premium_over_issuance_<UTC>.csv` в текущую
   папку (путь можно задать через `--out`). `railway run` выполняет команду локально с переменными сервиса, поэтому
   нужен доступный снаружи `DATABASE_URL`. Если база доступна только по внутреннему адресу (`*.railway.internal`),
   запускать через `railway ssh` внутри контейнера.
2. Проверить CSV: колонки `target`, `target_source` (`purchases` / `db` / `fallback`), `fallback`
   (`no_payments` / `past_date`), `db_leaked`, `action`. Выборочно сверить пару пользователей с «Сверкой» в дашборде.
3. Применить: `railway run python -m scripts.fix_premium_over_issuance --apply --yes`. Для пробы можно
   сначала передать `--limit 20`. Без `--yes` скрипт спросит подтверждение, а без терминала откажется (код 3).
   Не больше 2 PATCH в секунду: 300 пользователей обработаются примерно за 2,5 минуты. CSV перезаписывается
   с итогами (`fixed` / `error` / `skip`), админу приходит один алерт со сводкой.

**Что меняется:** у premium-сущности только `expireAt`: PATCH `{id, expireAt}` по числовому id из стрима, username
проверяется. Строка `subscriptions` с утёкшей датой (больше 5 лет, не bypass-only) после успешного PATCH укорачивается
до той же даты. Каждое изменение пишется в `subscription_reconciliation_log`, в журнал «Сверки»: даты до и после,
id платежей-доказательств, причина `bulk script fix_premium_over_issuance`. Перед каждым PATCH БД пользователя
перечитывается, так что продление, оплаченное после проверки, учитывается.

**Что не трогается никогда:** bypass-сущность (`username` = telegram id): её +10 лет и ГБ. Заглушка +10 лет у
bypass-only строки в БД. Статус, лимит, сквады, тег и устройства premium. Платежи и балансы. Если PATCH не прошёл,
панель и БД остаются как были: ошибка считается и прогон идёт дальше.

Повторный запуск безопасен: исправленные сущности больше не старше 5 лет и в выборку не попадают.
Коды выхода: `0` — ок, `1` — есть ошибки по пользователям, `2` — БД или панель недоступны, `3` — не подтверждено.
