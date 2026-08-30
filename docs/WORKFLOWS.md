# WORKFLOWS — рецепты для агента

Пошаговые рецепты частых задач. Правила-инварианты — в корневом `CLAUDE.md` и вложенных
`CLAUDE.md` соответствующих папок; здесь — последовательность действий.

## Добавить платёжного провайдера

1. Новый `<name>_service.py` в корне репо по паттерну: `is_enabled()` (читает `<NAME>_*` из `config.py`)
   → `create_invoice/transaction()` (обёрнуть `retry_async`) → `process_webhook_data()`.
2. Верификация вебхука ОБЯЗАТЕЛЬНА (подпись/HMAC/RSA) + идемпотентность + сверка суммы. Если делаешь
   fail-open — это осознанный компромисс, задокументируй в коде причину.
3. Делегируй финализацию в `app/services/payments/confirmation.py::process_confirmed_payment` — НЕ
   реализуй выдачу подписки/трафика в самом сервисе.
4. Роут вебхука — в `app/api/`. Тест: `tests/test_webhook_signatures.py` — добавь кейс подписи.
5. Детали инвариантов — `app/services/payments/CLAUDE.md`.

## Добавить воркер

1. Скрипт в корне репо (рядом с `activation_worker.py` и т.п.), НЕ отдельный процесс.
2. Запусти как `asyncio.create_task(...)` в `main()` (там же, где остальные воркеры). Отдельного
   worker-дино/systemd/cron нет — деплой единый.
3. Петля: `while True: try: await do_one_iteration() except asyncio.CancelledError: raise
   except Exception as e: log_event(...); await asyncio.sleep(INTERVAL)`. Без «warm-up/recovery» обвязок.
4. **Идемпотентность** (безопасно перезапускать), сравнение времени по **UTC**, устойчивость к сетевым
   ошибкам (retry на следующем цикле, не бесконечный внутренний retry).
5. Логировать через `log_event()`; не логировать per-item внутри цикла.

## Добавить хендлер / команду

1. Файл в `app/handlers/{callbacks,user,payments,admin}/`. Зарегистрируй роутер в
   `app/handlers/__init__.py` (порядок: callbacks→user→payments→admin→game→`unknown` последним).
2. Все строки — `app.i18n.get_text(user_language, "namespace.key")`. Никакого хардкода текста и `"ru"`.
   Новый ключ добавь в `app/i18n/ru.py` и `en.py`.
3. Бизнес-логику НЕ пиши в хендлере — вызывай `app/services/<domain>/service.py`.
4. Сообщения — через `app/utils/telegram_safe.py::safe_send_message`.
5. Проверка «не потерял хендлер»: `grep -c "@router\."` до/после. Детали — `app/handlers/CLAUDE.md`.

## Добавить сервис

`app/services/<domain>/service.py` — вся бизнес-логика там; тонкий воркер/хендлер снаружи только
оркестрирует. Внешние вызовы (HTTP) — с явным `httpx.Timeout(connect/read/write/pool)`, один retry-слой
на call-site, circuit breaker на домен. **Не держать транзакцию БД открытой во время HTTP-вызова.**

## Добавить миграцию

1. Новый `migrations/NNN_<name>.sql` (следующий номер). **Backward-compatible**, откатываемая; допущения
   отката — комментарием в файле.
2. Код не должен полагаться на немедленное наличие новых полей.
3. Проверь локально, что применяется **на пустой БД** без ошибок (это и есть CI-гейт Migration Integrity).
4. Детали — `database/CLAUDE.md`.

## Тесты

`pytest tests/`. Юниты мокают БД (`mock_database`), детерминированное время (`mock_datetime`). Новый тест,
тянущий `config`, — ПОСЛЕ ENV-стаба в `conftest.py`. Философия: тестируем бизнес-логику, не инфраструктуру
(`tests/README.md`).
