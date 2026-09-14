# ATCbot — CLAUDE.md

Telegram-бот продажи VPN-подписок (Remnawave-бэкенд). aiogram 3.x, **webhook-режим**,
**один процесс** (бот + FastAPI + все воркеры как `asyncio.create_task`), Postgres на сыром
**asyncpg без ORM**, деплой на **Railway** (Docker multi-stage), админ-дашборд — отдельное
React SPA (WebAuthn). Вся бизнес-логика — в `app/services/<domain>/service.py`; тонкие
оркестраторы — в корне репо и `app/handlers/`.

## Команды

| Задача | Команда |
|--------|---------|
| Тесты | `pytest tests/` (`asyncio_mode=auto`; юниты мокают БД; 4 теста с настоящим Postgres без него пропускаются — CI гоняет pytest без Postgres, `postgres:16` нужен только гейту миграций) |
| Линт | `ruff check` + `python -m compileall` (набор нарочно узкий: `E9,F63,F7,F82,S,B`; стиль/unused-imports не блокируют) |
| Миграции | кастомный ранер `migrations.py` (НЕ alembic), файлы `migrations/NNN_*.sql` по номеру. Сейчас 77 файлов, последняя `083_pending_purchase_credit_kopecks.sql` |

> `tests/conftest.py` стабит ENV (`APP_ENV=stage`, `STAGE_BOT_TOKEN`…) через `os.environ.setdefault`
> **до** импорта `config.py`. Любой тест, тянущий `config`, обязан идти после этого блока.

## Архитектура (подсистемы)

- **`main.py` — порядок импортов неслучаен.** `setup_logging()` первым; `app.utils.button_defaults`
  (глобальный monkeypatch `InlineKeyboardButton.__init__`) — **раньше любого хендлера**. Middleware-цепь:
  Concurrency → ErrorBoundary → PrivateChatOnly → GlobalRateLimit → LastSeen. Один корневой роутер
  `app.handlers`.
- **Один процесс, воркеры = таски.** `activation_worker`, `auto_renewal`, `fast_expiry_cleanup`,
  `reminders`, `trial_notifications`, `admin_notifications`, `healthcheck`,
  `wata_reconciler` и др. стартуют как `asyncio.create_task(...)` в `main()` — отдельного
  worker-дино/systemd/cron нет. Advisory-lock Postgres даёт single-instance гарантию на весь lifetime.
- **БД:** `database/core.py` + доменные модули (`users`, `subscriptions`, `traffic`…). PostgreSQL,
  сырой asyncpg. Флаг `database.DB_READY` — guard деградированного режима.
- **Платежи:** 3 тонких провайдер-клиента (`platega`, `cryptobot`, `wata`) одного паттерна
  `is_enabled()→create_invoice/transaction()→process_webhook_data()` → единый финализатор
  `app/services/payments/confirmation.py::process_confirmed_payment`.
- **Деградация:** `app/core/system_state.py` (`ComponentStatus`) + `DB_READY` — бот отвечает даже при
  частично недоступной инфре, а не падает при первой ошибке.
- **Дашборд:** `dashboard/` — React18+Vite SPA, вход по **WebAuthn** (passkey, не логин/пароль),
  бэкенд `app/api/dashboard/`. Живая карта фичи — `docs/admin_dashboard_implementation_map.md`.

## Правила — NEVER

- **Корневой `/handlers.py` удалён** (чистка 2026-09). Экран выбора способа оплаты `/buy`
  (`show_payment_method_selection`) живёт в `app/handlers/payments/payment_method_selection.py`.
- **i18n — только `app.i18n.get_text(user_language, "namespace.key")`.**
- **Не хардкодить `"ru"` и не хардкодить текст в хендлере** — всегда `get_text` с языком юзера.
  (`docs/archive/root/LANGUAGE_REFACTOR_PLAN.md §1.2` помечает `localization.get_text("ru",…)` как Prohibited Pattern.)
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
- **`add_bypass_traffic` не идемпотентен по `purchase_id`** (только старый путь, флаг `USE_NEW_PROVISIONING` выключен; outbox идемпотентен по ключу задачи) → retry вебхука = double-add GB
  (осознанный компромисс «не терять платежи», см. комментарии в `confirmation.py`).
- **Подписи вебхуков — fail-closed.** `wata`: RSA-SHA512 по сырому телу; ключ из `WATA_PUBLIC_KEY_PEM`
  или ленивая загрузка (прогрев в `main.py`); нет ключа/крипто-либы/неверная подпись → 500, WATA
  ретраит до 32 ч, платёж не теряется. Lava удалена целиком (чистка 2026-09), `/webhooks/lava` → 404;
  WATA-кнопки гейтятся `wata_service.is_enabled()`.
- **Удалено 2026-09-14 (решение владельца):** бизнес-тарифы, вывод средств, старая админка в боте.
  Легаси `biz_*` в БД = Plus везде через одно правило `app/services/tariffs.normalize_tier()` — новых
  проверок `biz` не добавлять. Админка в боте = только `app/handlers/admin/base.py` (`/admin`: ссылка на
  дашборд, «Написать пользователю», сброс пароля; чат; `/platega_sub_status`) + 🔒 выдача заказов
  магазина. Новые админ-функции — только в дашборде. Решения: `docs/audit/SCOPE.md`.
- **`app/utils/referral_middleware.py`** лежит не с остальными middleware (`app/core/*_middleware.py`) —
  известное расхождение, не баг.
- **Node-sidecar:** корневой `package.json` (`atlas-bot-incy-sidecar`) — не фронтенд, а `@incy/link-encoder`
  для `app/services/incy_crypto.py` (`node scripts/incy_encode.mjs`). `to_incy_link()` сначала пробует
  crypt1 через Node; без Node отдаётся plain `incy://add/<url>` (кнопка не скрывается).
- **`ruff` игнорит `S608` (SQL-инъекция)** с пометкой «false positive: whitelist-validated» — при ревью
  f-string SQL проверять по факту, не доверять самодекларации в конфиге.
- **enterprise-readiness шаблон** (change_management, compliance, incidents, rfc, security/, region_failover,
  multi-region, SOC2, «1M users», «5 команд») перенесён в `docs/archive/` — **аспирационный шаблон, разошедшийся
  с реальностью** (одно-региональный бот на Railway). НЕ источник истины по архитектуре. Реально
  проектно-специфичны (остались в `docs/`): `security_model.md`,
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
- **`docs/AGENT_AUDIT_MAP.md`** — карта старых `*AUDIT*.md` (теперь в `docs/archive/root/`): что где, статусы, tree-first-предупреждение.
- **`docs/archive/`** — исторические документы (старые аудиты, планы, legacy samopis/Xray), не отражают текущее
  состояние. Актуальный аудит — `docs/audit/`.

## Аудиторские документы (`docs/audit/` — актуальные, `docs/archive/root/*AUDIT*.md` — исторические)

Старые `*AUDIT*.md` — логи ремедиации/верификации, источник инвариантов, которые нельзя регрессировать (напр. `FOR UPDATE`
на операциях с балансом, порядок финализации платежа, idempotency-флаги). `docs/archive/root/COMPREHENSIVE_CODE_AUDIT_2026_03.md` и
раздел «IMMEDIATE ACTIONS (MUST FIX)» в `docs/archive/root/FULL_PRODUCTION_AUDIT.md` могут содержать **ещё открытые** пункты —
сверять с текущим кодом. Читать только tree-first (`grep -nE '^#{1,3} '` по одному файлу → узкий Read
нужного раздела); bulk-разбор всех audit-файлов сразу ложно триггерит cyber-классификатор. Полная
карта — `docs/AGENT_AUDIT_MAP.md`.
