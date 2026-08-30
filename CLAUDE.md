# ATCbot — CLAUDE.md

Telegram-бот продажи VPN-подписок (Remnawave-бэкенд). aiogram 3.x, **webhook-режим**,
**один процесс** (бот + FastAPI + все воркеры как `asyncio.create_task`), Postgres на сыром
**asyncpg без ORM**, деплой на **Railway** (Docker multi-stage), админ-дашборд — отдельное
React SPA (WebAuthn). Вся бизнес-логика — в `app/services/<domain>/service.py`; тонкие
оркестраторы — в корне репо и `app/handlers/`.

## Команды

| Задача | Команда |
|--------|---------|
| Тесты | `pytest tests/` (`asyncio_mode=auto`; юниты мокают БД, полный прогон требует `postgres:16`) |
| Линт | `ruff check` + `python -m compileall` (набор нарочно узкий: `E9,F63,F7,F82,S,B`; стиль/unused-imports не блокируют) |
| Миграции | кастомный ранер `migrations.py` (НЕ alembic), файлы `migrations/NNN_*.sql` по номеру. Сейчас ~79 |

> `tests/conftest.py` стабит ENV (`APP_ENV=stage`, `STAGE_BOT_TOKEN`…) через `os.environ.setdefault`
> **до** импорта `config.py`. Любой тест, тянущий `config`, обязан идти после этого блока.

## Архитектура (подсистемы)

- **`main.py` — порядок импортов неслучаен.** `setup_logging()` первым; `app.utils.button_defaults`
  (глобальный monkeypatch `InlineKeyboardButton.__init__`) — **раньше любого хендлера**. Middleware-цепь:
  Concurrency → ErrorBoundary → PrivateChatOnly → GlobalRateLimit → LastSeen. Один корневой роутер
  `app.handlers`.
- **Один процесс, воркеры = таски.** `activation_worker`, `auto_renewal`, `fast_expiry_cleanup`,
  `reminders`, `trial_notifications`, `broadcast_service`, `admin_notifications`, `healthcheck`,
  `wata_reconciler`, `site_sync` и др. стартуют как `asyncio.create_task(...)` в `main()` — отдельного
  worker-дино/systemd/cron нет. Advisory-lock Postgres даёт single-instance гарантию на весь lifetime.
- **БД:** `database/core.py` + доменные модули (`users`, `subscriptions`, `traffic`…). PostgreSQL,
  сырой asyncpg. Флаг `database.DB_READY` — guard деградированного режима.
- **Платежи:** 4 тонких провайдер-клиента (`platega`, `lava`, `cryptobot`, `wata`) одного паттерна
  `is_enabled()→create_invoice/transaction()→process_webhook_data()` → единый финализатор
  `app/services/payments/confirmation.py::process_confirmed_payment`.
- **Деградация:** `app/core/system_state.py` (`ComponentStatus`) + `DB_READY` — бот отвечает даже при
  частично недоступной инфре, а не падает при первой ошибке.
- **Дашборд:** `dashboard/` — React18+Vite SPA, вход по **WebAuthn** (passkey, не логин/пароль),
  бэкенд `app/api/dashboard/`. Живая карта фичи — `docs/admin_dashboard_implementation_map.md`.

## Правила — NEVER

- **Не редактировать `/handlers.py`** (корень, 49 КБ) — замороженный снапшот старого монолита,
  `@router.` = 0, `main.py` его не импортирует. Реальный роутинг — в `app/handlers/*`.
- **Не импортировать `app.core.i18n`** — сломан (нет `manager.py`, 0 импортов). Только
  `app.i18n.get_text(user_language, "namespace.key")`.
- **Не хардкодить `"ru"` и не хардкодить текст в хендлере** — всегда `get_text` с языком юзера.
  (`LANGUAGE_REFACTOR_PLAN.md §1.2` помечает `localization.get_text("ru",…)` как Prohibited Pattern.)
- **Не трогать `systemd/vpn-api.service`** — мёртвый легаси samopis-Xray, убит при cutover на
  Remnawave 3.x. Держится как исторический артефакт.
- **Не держать открытое соединение/транзакцию БД во время HTTP-вызова.** Железное правило.
- **Не делать retry-внутри-retry.** Один retry-слой на call-site.

## Правила — ALWAYS

- **UTC-контракт (частый источник багов).** Колонки `TIMESTAMP WITHOUT TIME ZONE`, asyncpg ждёт naive.
  В БД пишем через `_to_db_utc()` (кидает при не-aware-UTC), читаем через `_from_db_utc()`.
- **Логировать через `log_event()`** (`app/core/structured_logger.py`), поля
  component/operation/correlation_id/outcome/duration_ms/reason; таксономия ошибок
  infra/dependency/domain/unexpected. Не логировать секреты/PII/полные payload'ы и не per-item в циклах.
  Grep-able теги вида `REMNAWAVE_CREATE: …`, `SUB_AGGREGATOR_CMD_ENTERED …` — устоявшаяся конвенция.
- **Слать сообщения через `safe_send_message`** (`app/utils/telegram_safe.py`), не голый `bot.send_message`.
- **Retry — только `retry_async`** (`app/utils/retry.py`): `DEFAULT_RETRIES=2`, `DEFAULT_MAX_DELAY=10.0` —
  единственный источник правды по ретраям.
- **Платёжные вебхуки:** проверка подписи + идемпотентность + валидация суммы — обязательно.
- **Admin-функции:** верификация `telegram_id` админа + запись в audit-лог.
- **Финансовая идемпотентность — неприкасаемый инвариант:** no UUID loss, no double activation,
  no double payment, no subscription loss; финансовые мутации = одна транзакция, один коннекшн.
- **Новая миграция обязана применяться на пустой БД без ошибок** (CI-гейт Migration Integrity) и быть
  backward-compatible. Принцип: Compatibility > Cleanliness.

## Gotchas

- **Два payment-pipeline:** `confirmation.py` (внешние провайдеры) vs `app/services/payments/service.py`
  (Telegram-native/Stars). Не путать точки входа при дебаге.
- **`add_bypass_traffic` не идемпотентен по `purchase_id`** → retry вебхука = double-add GB
  (осознанный компромисс «не терять платежи», см. комментарии в `confirmation.py`).
- **Fail-open на верификации подписи** у части провайдеров: `lava` при пустом `LAVA_SIGN_KEY`, `wata` если
  не удалось подтянуть публичный ключ (кешируется в процессе, не переживает деплой). Сознательный trade-off.
- **Два i18n-модуля** (`app/i18n/` живой, `app/core/i18n/` мёртвый); `app/utils/referral_middleware.py`
  лежит не с остальными middleware (`app/core/*_middleware.py`) — известные расхождения, не баги.
- **Node-sidecar:** корневой `package.json` (`atlas-bot-incy-sidecar`) — не фронтенд, а `@incy/link-encoder`
  для `app/services/incy_crypto.py` (`node scripts/incy_encode.mjs`). Без Node бот работает, но кнопка
  «Открыть в Incy» молча скрывается.
- **`ruff` игнорит `S608` (SQL-инъекция)** с пометкой «false positive: whitelist-validated» — при ревью
  f-string SQL проверять по факту, не доверять самодекларации в конфиге.
- **`docs/` enterprise-readiness** (change_management, compliance, incidents/runbooks, multi-region, SOC2,
  «1M users», «5 команд») — **аспирационный шаблон, разошедшийся с реальностью** (одно-региональный бот на
  Railway). НЕ источник истины по архитектуре. Реально проектно-специфичны: `security_model.md`,
  `capacity_limits.md`, `load_shedding.md`, `ownership.md`, `data_ownership.md`.

## Рецепты

- **Новый хендлер:** файл в `app/handlers/{callbacks,user,payments,admin}/`, зарегистрировать в
  `app/handlers/__init__.py` (порядок: callbacks→user→payments→admin→game→`unknown` последним). Все
  строки — через `get_text`. Валидация «не потерял хендлер»: `grep -c "@router\."` до/после.
- **Новый сервис:** `app/services/<domain>/service.py` — вся логика там; тонкий воркер/хендлер снаружи.
- **Новый платёжный провайдер:** тонкий клиент `is_enabled/create_invoice/process_webhook_data` →
  делегирует в `confirmation.process_confirmed_payment`. Подпись + идемпотентность + сверка суммы.
- **Новый воркер:** `asyncio.create_task` в `main()`; петля
  `while True: try: await do_one() except CancelledError: raise except Exception as e: log(e); sleep(INTERVAL)`;
  идемпотентность, сравнение времени по UTC.

## Доп. файлы для агента

- **Вложенные `CLAUDE.md`** (подгружаются лениво при работе в папке): `app/services/payments/`,
  `app/handlers/`, `database/`, `dashboard/` — глубокие правила по подсистеме.
- **`docs/WORKFLOWS.md`** — пошаговые рецепты (добавить провайдера/воркер/хендлер/сервис/миграцию).
- **`docs/AGENT_AUDIT_MAP.md`** — карта `*AUDIT*.md`: что где, статусы, tree-first-предупреждение.

## Аудиторские документы (`*AUDIT*.md`, `docs/`)

Это логи ремедиации/верификации — источник инвариантов, которые нельзя регрессировать (напр. `FOR UPDATE`
на withdrawal, порядок финализации платежа, idempotency-флаги). `COMPREHENSIVE_CODE_AUDIT_2026_03.md` и
раздел «IMMEDIATE ACTIONS (MUST FIX)» в `FULL_PRODUCTION_AUDIT.md` могут содержать **ещё открытые** пункты —
сверять с текущим кодом. Читать только tree-first (`grep -nE '^#{1,3} '` по одному файлу → узкий Read
нужного раздела); bulk-разбор всех audit-файлов сразу ложно триггерит cyber-классификатор. Полная
карта — `docs/AGENT_AUDIT_MAP.md`.
