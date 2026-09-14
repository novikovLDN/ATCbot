# 05. Готовность `refactor/audit-2026-09` к выкатке в прод

- Дата: 2026-09-13. Прод = `main` = `cf8205fc` (`origin/main`; новых коммитов на `main` после форка нет: `git log HEAD..origin/main` пусто).
- Проверялась ветка на `73a38011`, это 105 коммитов поверх `cf8205fc`.
- Окружение: macOS, Python 3.11.15 (`.venv`), Postgres 16 через `pgserver`. **Docker нет**, образ локально не собирался.
- Скрипты проверки лежат в scratchpad сессии, `prodready/`: `boot.py`, `snap.py`, `smoke.py`, `routes.py`, `envscan.py`, `sigterm_test.py`. В репозиторий они не входят.

## Вердикт: **ГОТОВО С УСЛОВИЯМИ**

**Блокеров в коде нет.**
- Апгрейд схемы проходит на копии прод-схемы с данными. Данные не меняются.
- Откат на `main` работает без отката БД.
- Приложение стартует, воркеры живут, вебхуки на прежних путях.
- Тесты зелёные. Новых сирот-кнопок и пропавших i18n-ключей нет.

### Условия (выполнить до мержа или деплоя)

| # | Условие | Почему |
|---|---|---|
| У1 | **Запушить ветку или открыть PR и дождаться `CI OK` на GitHub.** | Ветки нет на `origin` (`git ls-remote --heads origin refactor/audit-2026-09` пусто, `gh run list --branch refactor/audit-2026-09` → `[]`). Новый CI ни разу не запускался. **`Docker Build` и `Frontend Build` не проверены вообще:** здесь нет Docker, а `dashboard/dist` собирается только в образе. |
| У2 | **Отменить живые рекуррентные подписки Platega** в кабинете (RUNBOOK §2.3). | Код рекуррента удалён. Колбэк списания даёт только алерт, доступ не выдаётся (проверено пробой: `/webhooks/platega-subscription` → `200 recurring_disabled` + алерт). |
| У3 | **WATA: действующий `PROD_WATA_ACCESS_TOKEN`, `PROD_WATA_SANDBOX=false`.** Ключ подписи: закрепить в `PROD_WATA_PUBLIC_KEY_PEM` или оставить ленивую загрузку (RUNBOOK §2.4). | Подпись теперь fail-closed. Без ключа webhook WATA отвечает 500. WATA повторяет до 32 ч, так что платёж не теряется, но выдача задерживается. |
| У4 | **`PROD_DASHBOARD_BASE_URL` = точный origin дашборда** (схема + хост). | Мутации с cookie-сессией без совпадающего `Origin`/`Referer` получают 403 (`security.py:77-101`). |
| У5 | **Бэкап БД и проверка размеров таблиц** перед деплоем (RUNBOOK §2.5, §2.6). | 081 строит 7 индексов **без `CONCURRENTLY`** на соединении пула с `command_timeout=30` с (`database/core.py:250`, `DB_POOL_COMMAND_TIMEOUT`). Если построение не уложится в 30 с, миграция упадёт. Бот уйдёт в деградированный режим и будет падать на том же месте при каждом повторе. При текущих объёмах это доли секунды. Если `users` + `subscriptions` + `audit_log` — миллионы строк, на первый деплой задать `DB_POOL_COMMAND_TIMEOUT=300` (без префикса). |
| У6 | **`PROD_USE_NEW_PROVISIONING=off`** на первом деплое. | Код по умолчанию `off` (`provisioning_flags.py:41`). Явное значение нужно, чтобы было видно в Railway. |
| У7 | **Lava.** PR #772 на 2026-09-13 всё ещё **OPEN** (`gh pr view 772` → `"state":"OPEN"`). | До деплоя ветки прод уязвим через `/webhooks/lava` (`00_recon.md` §P0-A). Деплой ветки закрывает уязвимость: пробa `POST /webhooks/lava` → **404**. `PROD_LAVA_*` ветка не читает. Владелец держит их до деплоя, потому что на `main` кнопка WATA гейтится Lava. После деплоя их можно удалить. |

### Замечания (не блокеры; всё, кроме Н6, есть и на `main`)

- **Н1. SIGTERM не проходит graceful shutdown.**
  - Процесс умирает сразу: `rc=-15` за 0,1 с (`sigterm_test.py`). Блок `finally` в `main()` не выполняется: нет `WEBHOOK_DELETED` и `Advisory lock released`.
  - Это безопасно:
    - advisory lock снимается вместе с соединением;
    - `running`-задачи `provisioning_jobs` забираются повторно после lease 120 с;
    - Telegram повторит недоставленные апдейты.
  - `main.py` в части shutdown не менялся.
- **Н2. Advisory lock при перекрытии деплоев.**
  - В PROD новый инстанс, не получивший lock за 1 с, выходит: `Advisory lock not acquired in PROD` → `sys.exit(1)` (`main.py:229-231`). Railway его перезапустит.
  - Миграции к этому моменту уже применены: они идут до lock, а 081/082 additive.
  - В логах деплоя это ожидаемо, если прекращается после остановки старого инстанса.
- **Н3. i18n.**
  - Ключа `errors.database_unavailable` нет ни в `ru`, ни в `en`. Пользователь увидит сырой ключ в 11 местах `app/handlers/game.py` (188, 356, 868, 947, 1001, 1051, 1101, 1145, 1211, 1280, 1338). Бывает только при недоступной БД.
- **Н4. Docker.**
  - Корневого `package-lock.json` нет ни на `main`, ни в ветке. `npm install --omit=dev` для Incy-sidecar не зафиксирован по версиям.
  - CI `Docker Build` проверяет наличие `node_modules/@incy/link-encoder`.
- **Н5. Миграция 013 на `main` не накатывается на пустую БД.**
  - Ошибка `UndefinedColumnError first_paid_at`, воспроизведено. В ветке 013 исправлена.
  - На проде 013 давно записана в `schema_migrations` и повторно не выполняется.
- **Н6. Старые magic-ссылки на 30 дней перестают работать** — намеренно. `GET /dashboard/api/auth/verify` удалён.
- **Н7. Сырой `bot.send_message` в `app/handlers/payments/payment_method_selection.py:106`.**
  - Это перенос кода из удалённого `handlers.py` (`e7cbf594`): fallback после `send_photo`, новой логики нет.
  - Прочие сырые вызовы в админ-хэндлерах были и раньше.
- **Н8. Кнопки удалённых функций** (Lava, `admin:mig_*`) в старых сообщениях теперь отвечают тостом «кнопка устарела» (`common.button_outdated`). На `main` такая кнопка просто висела.
- **Н9. На `main` последний запуск workflow `Deploy` — `failure`** (`gh run list`). Это старый `deploy.yml`, в этой проверке не разбирался. Ветка его заменяет.

---

## Проверка 1. Путь апгрейда (главное)

**Метод.**
- Временный worktree на `cf8205fc` и пустая БД `upg` в pgserver (PG 16).
- `boot.py` делает то же, что `main.py`: `database.init_db()`, `APP_ENV=local`.

**1a. Прод-схема кодом `main`.**

```
boot.py <main> upg   → init_db=False
asyncpg.exceptions.UndefinedColumnError: column "first_paid_at" of relation "referrals" does not exist
```

`main` сам не поднимается на пустой БД (Н5). Прод-схема старше миграций, колонку когда-то создал inline-DDL. Эмуляция прода:
- `ALTER TABLE referrals ADD COLUMN IF NOT EXISTS first_paid_at TIMESTAMP;` (так было на проде);
- повторный boot **немодифицированного** `main`.

```
INFO migrations: Found 74 migration files
BOOT_RESULT … init_db=True DB_READY=True
```

**1b. Данные** (`snap.py seed`):

| Таблица | Строки | Что внутри |
|---|---|---|
| `users` | 6 | реферер, баланс, триал, без подписки |
| `subscriptions` | 5 | active plus (auto_renew); expired basic; bypass-only (`is_bypass_only`, +10 лет); trial; combo с `activation_status='pending'` |
| `pending_purchases` | 5 | paid, pending (wata, combo), expired (cryptobot), balance_topup, traffic_pack |
| `payments` | 3 | |
| `referrals` | 2 | один rewarded |
| `balance_transactions` | 2 | |
| `platega_subscriptions` | 1 | Active |

Снимок до апгрейда: 53 таблицы, 73 версии (последние `078, 079, 080`), 155 индексов.

**1c. Апгрейд кодом ветки на той же БД.**

```
INFO migrations: Found 76 migration files
INFO migrations: Applying migration 081: 081_users_list_dashboard_indexes.sql
INFO migrations: Migration 081 applied successfully
INFO migrations: Applying migration 082: 082_provisioning_jobs.sql
INFO migrations: Migration 082 applied successfully
BOOT_RESULT … init_db=True DB_READY=True
```

`snap.py compare` с колонками «до», поэтому добавленные колонки не считаются изменением данных:

```
DATA CHANGED schema_migrations: rows 73->75
tables compared=53 data/columns changed=1          ← только schema_migrations
tables added: ['provisioning_jobs']
columns: +19 -0 ~0     (18 × provisioning_jobs.*, platega_subscriptions.is_combo)
indexes: +11 -0 ~0     (7 из 081, 4 из 082)
constraints: +4 -0 ~0  (все на provisioning_jobs)
migrations added: ['081', '082'] removed: []
```

Все 52 остальные таблицы совпали **по числу строк и по md5 содержимого**. Inline-DDL `database/core.py` в ветке не менялся: в диффе нет `CREATE`/`ALTER`.

**1d. Откат: код `main` на обновлённой БД.**

```
BOOT_RESULT <main> … init_db=True DB_READY=True      (Found 74 migration files, ничего не применено)
tables compared=54 data/columns changed=0; columns/indexes/constraints +0 -0 ~0; migrations added [] removed []
```

Плюс полный старт приложения `main` на этой БД (`smoke.py`):
- 13 живых задач, `/health` → 200 `database: connected`;
- advisory lock взят, graceful cancel прошёл.

**Откат образа без отката БД работает.**

**Новые миграции относительно `main`:** ровно `081_users_list_dashboard_indexes.sql` и `082_provisioning_jobs.sql`.
- Файлов 76 против 74. Версий 75 против 73: дубль `006` учитывается один раз.
- Изменён текст `013`. На проде не перевыполняется: версия записана, раннер без контрольных сумм.
- `081` в `atcnew` идентична: `git diff refactor/audit-2026-09 atcnew -- migrations/` показывает только `013` и `082`. Будущий выкат `atcnew` конфликта номеров не даст.

## Проверка 2. Переменные окружения `main` → ветка

**Метод.** AST-скан всех чтений `config.env("X")`, `os.getenv`, `os.environ[...]`/`.get` и `_envbool` в обоих деревьях (`envscan.py`, `envdiff.py`). Тесты, доки и дашборд не сканировались.

- **Новых обязательных переменных нет.** Обязательные те же, что на `main`: `BOT_TOKEN`, `ADMIN_TELEGRAM_ID`, `DATABASE_URL`, `WEBHOOK_URL`, `WEBHOOK_SECRET`. Без них `config.py` делает `sys.exit`.
- **Дефолты общих переменных не изменены** (`envdiff.py`: `CHANGED` только у динамических и CI-переменных).
- **`USE_NEW_PROVISIONING` по умолчанию `off`** (`provisioning_flags.py:41`). Невалидное значение → `off` + warning.
- `NEW_PROVISIONING_ENTRYPOINTS` пустое = **все точки** при `on` (`provisioning_flags.py:48-51`).

**Новые (все необязательные):**

| Переменная | Дефолт | Где |
|---|---|---|
| `USE_NEW_PROVISIONING` | `off` | `app/services/provisioning_flags.py:41` |
| `NEW_PROVISIONING_ENTRYPOINTS` | пусто (= все при `on`) | `provisioning_flags.py:49` |
| `WATA_PUBLIC_KEY_PEM` | пусто (ленивая загрузка) | `config.py:365` |
| `BRAND_NAME`, `BRAND_SHORT`, `BRAND_LOGO_URL`, `BRAND_PRIMARY_COLOR`, `BRAND_SUPPORT_URL`, `BRAND_CHANNEL_URL`, `BRAND_BOT_USERNAME` | текущий брендинг «Atlas Secure» | `app/branding.py`: `PROD_BRAND_X`, затем `BRAND_X`, затем дефолт |

**Удалённые (ветка их не читает, можно удалить после деплоя):**
- `LAVA_WALLET_TO`, `LAVA_JWT_TOKEN`, `LAVA_SIGN_KEY`, `LAVA_SHOP_ID`, `LAVA_API_URL`;
- `SUBSCRIPTION_PROXY_ENABLED`, `LEGACY_SAMOPIS_SUB_BASE_URL`;
- `XRAY_API_URL`, `XRAY_API_KEY`, `XRAY_API_TIMEOUT`, `XRAY_SYNC_ENABLED`;
- `VPN_SERVER_URL`, `VPN_PROVISIONING_ENABLED`, `SUB_BASE_URL`, `SITE_API_URL`, `SITE_BOT_API_KEY`;
- `MIGRATION_LOG_DIR` (без префикса).

Что изменится при удалении:
- `VPN_PROVISIONING_ENABLED` на `main` и так перезаписывался статусом Remnawave (`config.py` на `cf8205fc`). Поведение не меняется.
- `SITE_*`: удалён `site_sync`. Если на проде задан `PROD_SITE_API_URL`, синхронизация с сайтом прекратится. Это решение владельца, SCOPE.md.
- `SUBSCRIPTION_PROXY_ENABLED`: если на проде `true`, пропадут `/sub/{uuid}` и `/api/sub/{token}`. Выяснить до деплоя, RUNBOOK §2.2.

Ссылок в коде ветки на удалённые атрибуты `config.*` нет (`git grep` → пусто).

### Что выставить в Railway (сервис прода)

| Действие | Переменная | Значение |
|---|---|---|
| **задать** | `PROD_USE_NEW_PROVISIONING` | `off` |
| **не задавать** (пока `off`) | `PROD_NEW_PROVISIONING_ENTRYPOINTS` | пусто. При переходе на `on` **всегда** перечислять точки явно |
| проверить | `PROD_WATA_ACCESS_TOKEN`, `PROD_WATA_SANDBOX` | действующий токен, `false` |
| рекомендуется | `PROD_WATA_PUBLIC_KEY_PEM` | PEM из `GET /api/h2h/public-key` (RUNBOOK §2.4) или пусто |
| проверить | `PROD_JWT_SECRET`, `PROD_DASHBOARD_BASE_URL` | обе заданы. `DASHBOARD_BASE_URL` = точный origin |
| проверить | `PROD_REDIS_URL` | желательно. Без него сессии, блокировки логина и Idempotency-Key живут в памяти |
| проверить | `PROD_TRIAL_BYPASS_MB` | `500` или не задана |
| без изменений | `PROD_BOT_TOKEN`, `PROD_ADMIN_TELEGRAM_ID`, `PROD_DATABASE_URL`, `PROD_WEBHOOK_URL`, `PROD_WEBHOOK_SECRET`, `PROD_PLATEGA_*`, `PROD_CRYPTOBOT_API_TOKEN`, `PROD_REMNAWAVE_*`, `PROD_PURCHASE_FLOW_REMNAWAVE` | |
| по ситуации | `DB_POOL_COMMAND_TIMEOUT` (без префикса) | `300` только на первый деплой при очень больших таблицах (У5), затем убрать |
| необязательно | `PROD_BRAND_*` | дефолты равны текущему брендингу |
| **удалить после деплоя** | `PROD_LAVA_*` (5 шт.), `PROD_SUBSCRIPTION_PROXY_ENABLED`, `PROD_LEGACY_SAMOPIS_SUB_BASE_URL`, `PROD_XRAY_*`, `PROD_VPN_SERVER_URL`, `PROD_VPN_PROVISIONING_ENABLED`, `PROD_SUB_BASE_URL`, `PROD_SITE_API_URL`, `PROD_SITE_BOT_API_KEY`, `MIGRATION_LOG_DIR` | ветка их не читает. При откате на `main` см. RUNBOOK §6.2 |

## Проверка 3. Старт приложения

**Метод.** `smoke.py` запускает настоящий `main.main()`.
- Окружение: `APP_ENV=stage`, фейковые токены, Platega, CryptoBot и WATA включены фейковыми кредами, Remnawave и Redis не заданы.
- БД: пустая pgserver-база `smoke_branch`.
- Telegram Bot API подменён в процессе, в сеть к Telegram запросов нет.
- Через 35 с: список задач, HTTP-пробы, затем отмена `main()`.

**Старт.** Миграции 001…082 на пустой БД, затем:

```
Advisory lock acquired
Wata reconciler task started (interval=5min)
Provisioning worker task started (interval=15.0s)
PROVISIONING_WORKER started (interval=15.0s, max_jobs=50, job_timeout=45.0s, lease=120s)
WATA_PUBLIC_KEY_WARMUP: ok=True pinned=False
SMOKE main_task_done_early=False t=35.7s
SMOKE alive_tasks: 14
   activation_worker_task, auto_renewal_task, farm_notifications_task, fast_expiry_cleanup_task,
   health_check_task, main, provisioning_worker_task, reminders_task, run_admin_notifier,
   run_scheduled_broadcasts_worker, run_trial_scheduler, wata_reconciler_task, Server.serve, LifespanOn.main
SMOKE advisory_lock_rows: 1
```

За 35 с ни одна задача не упала. `main` на той же конфигурации дал 13 задач: в ветке добавился `provisioning_worker_task`.

**Graceful shutdown** (отмена `main()`): `WEBHOOK_DELETED` → `shutdown_tasks_cancelled` → `Advisory lock released` → `Database connection pool closed` → `Bot session closed` → `shutdown_completed`. Единственный traceback — `CancelledError` из lifespan uvicorn после `shutdown_completed`. Это шум.

SIGTERM: см. Н1.

**HTTP-пробы (ветка / `main` на обновлённой БД):**

| Проба | Ветка | `main` |
|---|---|---|
| `GET /health` | 200 `{"database":"connected",…,"status":"ok"}` | 200 |
| `POST /webhooks/lava` | **404** | 200 `disabled` (Lava-env не задан) |
| `POST /webhooks/wata` без подписи | **500** `transient_error` | 200 `invalid_signature` |
| `POST /webhooks/wata` с неверной подписью | **500** `transient_error` | 200 `invalid_signature` |
| `POST /webhooks/cryptobot` с неверной подписью или без неё | 200 `unauthorized` | 200 `unauthorized` |
| `POST /webhooks/platega` без кредов или с неверным секретом | 200 `unauthorized` | 200 `unauthorized` |
| `POST /webhooks/platega` с верными кредами и неизвестной покупкой | 200 `invalid` | 200 `invalid` |
| `POST /platega/callback` без кредов | 200 `unauthorized` | 200 `unauthorized` |
| `POST /webhooks/platega-subscription` с верными кредами | 200 `recurring_disabled` + forced-алерт | 200 `invalid` |
| `POST /telegram/webhook` без секрета | 403 | 403 |
| `GET /dashboard/api/branding` | 200 «Atlas Secure» | 404 (нет на `main`) |
| `GET /dashboard/` | 404: локально нет `dashboard/dist`, в образе собирается stage 1 | 404 |

Алерты админу за время смоук-теста в ветке: `[WATA_SIGNATURE] …` и `PAYMENT ALERT | Platega: callback по РЕКУРРЕНТНОЙ подписке …`. Оба ожидаемы.

**Маршруты FastAPI** (`routes.py`, дашборд включён):
- Платёжные и бот-пути **совпадают**: `/webhooks/{platega,cryptobot,wata,platega-subscription}`, `/platega/callback`, `/platega/subscription-callback`, `/telegram/webhook`, `/a/{token}`, `/a/_metrics`, `/a/_invalidate/{token}`, `/open/{client}`, `/health`, `WS /dashboard/ws`.
- Удалён только `POST /webhooks/lava`. `subscription_proxy` на `main` монтировался только при `SUBSCRIPTION_PROXY_ENABLED=true`.
- Дашборд: 139 → 151 маршрут.
  - Удалён `GET /dashboard/api/auth/verify`.
  - Добавлены `branding`, `branding/manifest.webmanifest`, `metrics/{cohorts,money,operations,overview,series,subscribers}`, `panel/{bandwidth,nodes,overview}`, `payments/kpi`, `users/list`.

**Хэндлеры aiogram** (обход дерева роутеров):
- `main`: 60 роутеров, `callback_query` 424, `message` 76, `pre_checkout_query` 1.
- Ветка: 59 роутеров, 387, 75, 1.
- Все убранные хэндлеры сняты намеренно: Lava (14), `admin/migration.py` (21), `admin/reconcile.py` (2) и мёртвые ещё на базе.
- Все 59 `Router()` достижимы. Порядок callbacks → user → payments → admin → game → unknown соблюдён.

**Сироты-кнопки** (AST-извлечение `callback_data` против фильтров):
- **Новых сирот нет.**
- 7 старых сирот, те же, что на базе, пользователю недостижимы: `broadcast_type:*`, `tariff_type:*` и голый `admin:user_reissue` в неиспользуемых клавиатурах.
- В магазине сирот нет.
- Новый catch-all `catch_unknown_callback` отвечает тостом `common.button_outdated`.

## Проверка 4. i18n

Метод: AST-скан `get_text` и алиасов, f-string-шаблонов и всех строк вида `namespace.key` в обоих деревьях. Поиск ключа идёт так: язык пользователя → `en` → сам ключ.
- Словари ru/en: 1144/1151 в ветке, 1151/1158 на базе. Прямых вызовов с литералом: 1433 в ветке.
- **Регрессий нет.**
  - Удалены 8 ключей: Lava, `renewal_*`, `start.site_linked_success`. Ни один нигде не используется.
  - Добавлен `common.button_outdated`.
- Уже было на `main`: `errors.database_unavailable` нет в обоих словарях (Н3).
  - 7 ключей есть только в en и не используются.
  - `buy.tariff_button_{tariff}` в устаревшей неиспользуемой `get_tariff_keyboard`.

## Проверка 5. Платежи

```
pytest tests/                               → 2637 passed, 4 skipped, 139 xfailed, 0 failed (22.5 s)
pytest tests/services/test_payment_matrix.py → 1074 passed, 127 xfailed (8.8 s)
ruff check . (0.15.22, как в CI)            → All checks passed!
python -m compileall                        → rc=0
```

- **`_STATUS_HTTP`** (`app/api/payment_webhook.py:52-76`):
  - Всё, что сервис **возвращает**, отдаётся с 200: `ok`, `already_processed`, `duplicate`, `amount_mismatch`, `provider_mismatch`, `rejected`, `invalid_*`, `not_found`, `invalid_status`, `error`, `unauthorized`, `invalid`, `ignored`, `disabled`, `refund_alerted`, `declined_notified`.
  - 500 — только `transient_error` и `timeout`, то есть брошенный `TransientPaymentError`.
  - Совпадает с `app/services/payments/CLAUDE.md`.
- **Fail-closed подтверждён пробами.**
  - WATA без подписи или с неверной → 500 + алерт `[WATA_SIGNATURE]`: WATA повторит.
  - CryptoBot с неверным HMAC и Platega с неверными кредами → `unauthorized`, ничего не зачисляется.
  - `/webhooks/lava` → 404.
- **Сырой `bot.send_message` в новом коде:** дифф `cf8205fc..HEAD` добавляет один вызов, `payment_method_selection.py:106`. Это перенос (Н7), новые платёжные уведомления идут через `safe_send_message` и `send_alert`.

## Проверка 6. Dockerfile и CI

- **Dockerfile** в ветке не менялся. `COPY . .` забирает всё нужное рантайму:
  - `app/` (включая `app/workers/`) и `database/`;
  - корневые модули `platega_service.py`, `wata_service.py`, `cryptobot_service.py`, воркеры;
  - `migrations/` (отдельный явный `COPY`);
  - `dashboard/dist` из stage 1;
  - Node и `npm install --omit=dev` для Incy.
- **`.dockerignore`** исключает `docs/`, `*.md`, `tests/`, `scripts/`, кроме `scripts/incy_encode.mjs`. Рантайм ничего из этого не читает: `git grep` нашёл только комментарии.
- Дифф `.dockerignore` убирает исключения для `scripts/migrate_samopis_to_remnawave.py` и `verify_samopis_migration.py` (их вызывал удалённый `admin/migration.py`) и шаблоны удалённых `translation_patch_*`. Корректно.
- **actionlint 1.7.12** по `ci.yml`, `deploy.yml`, `pr-checks.yml` → 0 замечаний.
- `ci.yml`: сервис `postgres:16`, `RUFF_VERSION: "0.15.22"` (тот же, что прогнан локально).
- `deploy.yml` срабатывает только на успешный CI по push в `main`/`stage` (`docs/ci.md`).
- **Docker локально не собирался.** Нужен зелёный `Docker Build` в CI (У1).

## Проверка 7. RUNBOOK

`docs/RUNBOOK.md` сверен с кодом и исправлен:
- шапка: коммит `73a38011`, 105 коммитов;
- §0: короткие чек-листы (новый раздел);
- статус PR #772 (OPEN);
- формулировка CSRF: 403 только при cookie-сессии;
- таймаут миграции 081 (`command_timeout`);
- совпадение 081 с `atcnew` проверено;
- описание гейта Migration Integrity;
- ожидаемые логи перекрытия деплоя и SIGTERM;
- номера строк логов: `BALANCE_TOPUP_OVERPAYMENT` `:5125`, `TRAFFIC_PACK_VIA_OUTBOX` `:1174`, `WEBHOOK_RETRY_REQUESTED` `:270`, `PROVISIONING_*` `:60/:123/:634/:641`, `DEAD_AFTER` `:64`, `run_migrations_safe` `:430`.

Метки `<ПРОВЕРИТЬ>`:
- **сброс сессий при смене `JWT_SECRET`:** ответ из кода — cookie-сессии хранятся в Redis/памяти под `dashboard:session:*` и от `JWT_SECRET` не зависят; смена секрета гасит только magic-ссылки/Bearer;
- **Stars за bypass-пакет (P0-F):** пути покупки пакета за Stars в ветке нет;
- **`TRIAL_BYPASS_GB`:** `.env.example` уже исправлен;
- **панель stage:** что делает код при пустых `STAGE_REMNAWAVE_*`.

Метка «healthcheck Railway = `/health`» кодом не решается: нет `railway.json`. Оставлена как проверка в UI.

---

## Чек-листы

### До деплоя

1. [ ] `CI OK` на GitHub для ветки или PR, включая `Docker Build`, `Frontend Build`, `Migration Integrity` (У1).
2. [ ] Живые рекуррентные подписки Platega отменены в кабинете (RUNBOOK §2.3) (У2).
3. [ ] `PROD_WATA_ACCESS_TOKEN` действует, `PROD_WATA_SANDBOX=false`, ключ WATA закреплён или выбрана ленивая загрузка (У3).
4. [ ] `PROD_JWT_SECRET` и `PROD_DASHBOARD_BASE_URL` заданы, origin точный (У4). Пароль или passkey админа есть, либо план войти по свежей ссылке `/admin` (RUNBOOK §2.8).
5. [ ] `PROD_SUBSCRIPTION_PROXY_ENABLED` не `true`, или последствия приняты (RUNBOOK §2.2).
6. [ ] `SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 3;` → верх `080`, `082` нет. Размеры `users`, `subscriptions`, `audit_log` проверены (У5).
7. [ ] Дамп БД снят и лежит вне Railway (RUNBOOK §2.5).
8. [ ] `PROD_USE_NEW_PROVISIONING=off`, `PROD_NEW_PROVISIONING_ENTRYPOINTS` пусто (У6).
9. [ ] Деплой в тихое время. Поддержка предупреждена: кнопки Lava и «Подписка СБП» исчезнут.

### Сразу после деплоя (15 минут)

**Логи Railway**, ожидаем:
- `Applying migration 081`, `Applying migration 082`, `Database migrations applied successfully`;
- `Advisory lock acquired`;
- `PROVISIONING_WORKER started`;
- `WATA_PUBLIC_KEY_WARMUP: ok=True`;
- `WEBHOOK_SET_SUCCESS`, `WEBHOOK_VERIFIED`, `UVICORN_STARTED`.

Кратковременный `Advisory lock not acquired in PROD` при перекрытии со старым инстансом допустим (Н2).

**Не должно быть:** `Migration … FAILED`, `DB INIT FAILED`, `CRITICAL`, `PROVISIONING_WORKER_TICK_ERROR`, `WATA_PUBLIC_KEY_WARMUP: failed`.

**HTTP:**
- `curl -s https://<PROD_HOST>/health` → 200, `"status":"ok"`;
- `curl -s -o /dev/null -w '%{http_code}' -X POST https://<PROD_HOST>/webhooks/lava -d '{}'` → `404`.

**SQL:**
- `SELECT version FROM schema_migrations WHERE version IN ('081','082');` → 2 строки;
- `SELECT count(*) FROM provisioning_jobs;` → 0.

**Бот** (тестовый аккаунт):
- `/start` → меню;
- «Купить» → способы оплаты: нет «Карта (Lava)» и «Подписка СБП», WATA видна;
- одна реальная покупка минимальной суммы через Platega или WATA → сообщение об успехе, доступ в профиле, `payment_errors` пусто;
- пополнение баланса → зачислена сумма счёта;
- оплата с баланса двойным тапом → одно списание;
- `mini_shop` открывается, цены прежние;
- старая кнопка из прежних сообщений → тост «кнопка устарела».

**Дашборд:**
- `/dashboard/` → вход;
- «Обзор», «Деньги», «Панель», «Операции» грузятся;
- любая мутация проходит, не 403.

**Алерты** в Telegram админа: не должно быть `PAYMENT ALERT` / `PROVISIONING` без причины. `[WATA_SIGNATURE]` допустим только если ключ ещё грузится.

### Откат

1. `PROD_USE_NEW_PROVISIONING=off`. Если ядро включали — дождаться 0 открытых задач (RUNBOOK §4.0, M1/M3). Если откат срочный, выгрузить `pending`/`running`/`dead` в CSV (RUNBOOK §6.2).
2. Railway → Deployments → предыдущий успешный деплой (`cf8205fc`) → **Redeploy**. Либо revert-PR в `main` силами владельца.
3. **БД не откатывать.** 081 и 082 additive: проверено, что код `main` стартует на обновлённой БД и ничего в ней не меняет. Строки `081`/`082` в `schema_migrations` не удалять.
4. Сразу после отката на `main` возвращаются:
   - уязвимость `/webhooks/lava`: если #772 не смержен, удалить `PROD_LAVA_*`, кнопка WATA при этом скроется;
   - прежнее поведение WATA и баланса.
   Env, удалённые после деплоя (`PROD_LAVA_*`, прокси), не возвращать. Исключение: прокси реально использовался.
5. Magic-ссылки на 30 дней, выпущенные до деплоя, на старом коде снова дают Bearer-доступ к API до истечения. Погасить их можно сменой `PROD_JWT_SECRET`. Cookie-сессии это не трогает: для них очистить ключи `dashboard:session:*` в Redis.
