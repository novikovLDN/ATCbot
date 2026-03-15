# COMPREHENSIVE CODE AUDIT — ATCbot (Atlas Secure VPN Bot)
## Date: 2026-03-15

---

# ЧАСТЬ 1. БЕЗОПАСНОСТЬ (SECURITY)

## 1.1 КРИТИЧЕСКИЕ ПРОБЛЕМЫ

### [S-CRIT-1] Platega webhook аутентификация — передача секрета в заголовке
**Файл:** `platega_service.py:149-158`
**Проблема:** Platega webhook верифицирует аутентичность запроса, проверяя `X-MerchantId` и `X-Secret` в заголовках. Но `X-Secret` — это статический секрет, передаваемый в plaintext в каждом запросе. Любой, кто перехватит один запрос, может подделывать вебхуки.
**Рекомендация:** Использовать HMAC-подпись тела запроса (как в CryptoBot), а не передачу секрета в заголовке. Если Platega API не поддерживает HMAC — добавить IP-фильтрацию для `/webhooks/platega`.

### [S-CRIT-2] Payment webhook endpoints возвращают 200 при ошибке
**Файл:** `app/api/payment_webhook.py:53-55`
**Проблема:** При exception в обработке Platega/CryptoBot вебхука возвращается `{"status": "error"}` со статусом 200. Платёжная система не сделает retry. Если произошла транзиентная ошибка (DB timeout), платёж может быть потерян.
**Рекомендация:** Возвращать HTTP 500 при транзиентных ошибках (DB unavailable), чтобы провайдер ретраил. HTTP 200 возвращать только для "already_processed", "ignored", "ok".

### [S-CRIT-3] MINI_APP_URL захардкожен в коде
**Файл:** `app/handlers/common/keyboards.py:17`
```python
MINI_APP_URL = "https://atlas-miniapp-production.up.railway.app"
```
**Проблема:** Production URL захардкожен. Если нужно сменить URL или использовать другой для STAGE/LOCAL — невозможно без изменения кода.
**Рекомендация:** Вынести в `config.py` через `env("MINI_APP_URL")`.

## 1.2 СРЕДНИЕ ПРОБЛЕМЫ

### [S-MED-1] SBP_DETAILS содержат тестовые данные в production config
**Файл:** `config.py:284-288`
```python
SBP_DETAILS = {
    "bank": "Банк",
    "account": "12345678901234567890",
    "name": "ИП Иванов Иван Иванович",
}
```
**Проблема:** Тестовые реквизиты в коде. Если они используются в production — пользователи увидят фиктивные данные.
**Рекомендация:** Перенести в переменные окружения или убрать, если SBP теперь через Platega API.

### [S-MED-2] Rate limiter использует threading.Lock в asyncio-приложении
**Файл:** `app/core/rate_limit.py:73,84,128-129`
**Проблема:** `threading.Lock` блокирует event loop при захвате. В высоконагруженных сценариях может привести к задержкам.
**Рекомендация:** Заменить на `asyncio.Lock` или переписать на полностью async-подход. Поскольку операции внутри лока быстрые (in-memory), это не критично, но архитектурно неверно.

### [S-MED-3] Rate limiter не имеет механизма очистки старых buckets
**Файл:** `app/core/rate_limit.py:128`
**Проблема:** `_buckets` dict растёт неограниченно. Каждый новый (telegram_id, action_key) создаёт bucket, который никогда не удаляется.
**Рекомендация:** Добавить периодическую очистку buckets, не использовавшихся более `window_seconds * 2`.

### [S-MED-4] Callback data validation пропускает admin callbacks без проверки
**Файл:** `app/utils/security.py:130-131`
```python
if callback_data.startswith("admin_"):
    return True, None  # Will be validated by authorization guard
```
**Проблема:** Любой callback, начинающийся с `admin_`, проходит валидацию. Если callback guard не установлен на всех admin handlers — возможен unauthorized access.
**Рекомендация:** Валидировать admin callbacks по whitelist конкретных паттернов.

### [S-MED-5] Advisory lock не блокирует запуск при ошибке
**Файл:** `main.py:225-229`
**Проблема:** Если advisory lock не получен (другой инстанс уже работает), бот продолжает работу. Два инстанса могут обрабатывать одни и те же подписки, создавая дубли.
**Рекомендация:** При невозможности получить advisory lock в production — делать sys.exit(1).

## 1.3 НИЗКИЕ ПРОБЛЕМЫ

### [S-LOW-1] Webhook secret сравнивается через `!=` вместо `hmac.compare_digest`
**Файл:** `app/api/telegram_webhook.py:41`
```python
if x_telegram_bot_api_secret_token != config.WEBHOOK_SECRET:
```
**Проблема:** Строковое сравнение через `!=` теоретически уязвимо к timing attack.
**Рекомендация:** Использовать `hmac.compare_digest()`.

### [S-LOW-2] `config.py` выводит информацию о конфигурации через print()
**Файл:** `config.py:56,71,326-331`
**Проблема:** Print-ы на уровне модуля выводятся при каждом импорте, включая тесты. Не критично для безопасности, но шумно.
**Рекомендация:** Перевести на logging.

### [S-MED-6] Health check alert может утечь DB connection string
**Файл:** `healthcheck.py:78`
```python
f"🚨 DB health check failed: {e}"
```
**Проблема:** Exception message может содержать DATABASE_URL с паролем, hostname и портом. Эта информация отправляется админу в Telegram (незашифрованный канал).
**Рекомендация:** Маскировать exception message: отправлять только `type(e).__name__`, не полный `str(e)`.

### [S-MED-7] pytest в production requirements
**Файл:** `requirements.txt`
**Проблема:** `pytest` и `pytest-asyncio` включены в production dependencies. Увеличивают attack surface и размер Docker image.
**Рекомендация:** Вынести в отдельный `requirements-dev.txt`.

### [S-MED-8] Referral код формата `ref_<telegram_id>` — enumerable
**Файл:** `app/services/referrals/service.py:72`
**Проблема:** Реферальный код напрямую содержит Telegram ID пользователя (`ref_123456`). Любой может перебором угадывать валидные реферальные коды.
**Рекомендация:** Использовать opaque токены (hash или random UUID) вместо прямого Telegram ID.

### [S-MED-7] confirmation.py — hardcoded Russian fallback в i18n_get_text
**Файл:** `app/services/payments/confirmation.py:182`
```python
text = i18n_get_text(
    language, "payment.success",
    f"🎉 Оплата получена!\n{_emoji} Тариф: {_label}\n📅 До: {expires_str}",
    ...
)
```
**Проблема:** Если i18n ключ `payment.success` отсутствует в языке пользователя, отображается русский fallback. Нерусскоязычные пользователи увидят русский текст.
**Рекомендация:** Убрать hardcoded fallback, полагаться на i18n fallback chain (язык → EN → ключ).

---

# ЧАСТЬ 2. КОРРЕКТНОСТЬ ЛОГИКИ

## 2.1 КРИТИЧЕСКИЕ ПРОБЛЕМЫ

### [L-CRIT-1] auto_renewal — баланс хранится в копейках, но сравнивается с рублями
**Файл:** `auto_renewal.py:228-231`
```python
user_balance_kopecks = subscription.get("balance", 0) or 0
balance_rubles = user_balance_kopecks / 100.0
if balance_rubles >= amount_rubles:
```
**Проблема:** `subscription.get("balance")` получает значение из JOIN с `users.balance`. Если `users.balance` хранит значение в рублях (как `float`), то деление на 100 даст некорректный результат. Нужно проверить, в каких единицах хранится баланс.
**Верификация:** В `database/users.py:get_user_balance` баланс возвращается как `float(balance) / 100.0`, что означает баланс в БД хранится в копейках. **JOIN в auto_renewal корректен**, но нужно проверить, что `u.balance` в JOIN возвращает именно kopecks.

### [L-CRIT-2] create_payment использует только 30-дневный период по умолчанию
**Файл:** `database/subscriptions.py:63-66`
```python
tariff_periods = config.TARIFFS.get(tariff, config.TARIFFS.get("basic", {}))
tariff_data = tariff_periods.get(30, {})
base_price = tariff_data.get("price", 149)
```
**Проблема:** Функция `create_payment` всегда использует цену за 30 дней, независимо от того, какой период выбрал пользователь. Параметр period_days не передаётся.
**Рекомендация:** Добавить параметр `period_days` в `create_payment` или удалить функцию, если она заменена новым purchase flow.

### [L-CRIT-3] Уведомление об автопродлении не локализовано
**Файл:** `auto_renewal.py:337-342`
```python
text = (
    "✅ Подписка продлена\n"
    f"📦/⭐️ Тариф: {tariff_label}\n"
    f"📅 До: {item['expires_str']}"
)
```
**Проблема:** Текст уведомления захардкожен на русском языке, хотя `language` доступен в `item["language"]`. Пользователи на других языках получат русский текст.
**Рекомендация:** Использовать `i18n.get_text(item["language"], "auto_renewal.success", ...)`.

### [L-CRIT-4] Activation worker — уведомление не локализовано
**Файл:** `activation_worker.py:211-215`
```python
text = (
    "🎉 Добро пожаловать в Atlas Secure!\n"
    f"{tariff_emoji} Тариф: {tariff_label}\n"
    f"📅 До: {expires_str}"
)
```
**Проблема:** Аналогично — текст захардкожен на русском. `language` получен, но не используется для форматирования текста.
**Рекомендация:** Использовать `i18n.get_text(language, "activation.success", ...)`.

## 2.2 СРЕДНИЕ ПРОБЛЕМЫ

### [L-MED-1] cmd_start — docstring внутри кода после return
**Файл:** `app/handlers/user/start.py:62`
```python
    return
    """Обработчик команды /start"""
```
**Проблема:** Docstring `"""Обработчик команды /start"""` расположен после `return` statement на строке 61. Это мёртвый код, но не вызывает ошибок.
**Рекомендация:** Переместить docstring на уровень функции.

### [L-MED-2] Gift activation — period_text не локализован
**Файл:** `app/handlers/user/start.py:110-114`
```python
if months == 1:
    period_text = "1 месяц"
elif months in (2, 3, 4):
    period_text = f"{months} месяца"
else:
    period_text = f"{months} месяцев"
```
**Проблема:** Склонение слова "месяц" захардкожено на русском.
**Рекомендация:** Использовать i18n ключи для pluralization.

### [L-MED-3] auto_renewal — VIP скидка применяется как int(base_price * 0.70)
**Файл:** `auto_renewal.py:219`
```python
amount_rubles = float(int(base_price * 0.70))
```
**Проблема:** Двойное преобразование (float → int → float) теряет точность. Для цены 149 × 0.70 = 104.3, int = 104, float = 104.0. Пользователь может недоплатить 30 копеек.
**Рекомендация:** Использовать единообразное округление: `round(base_price * 0.70, 2)`.

### [L-MED-4] cmd_start всегда показывает экран выбора языка
**Файл:** `app/handlers/user/start.py:205-207`
```python
text = i18n_get_text("ru", "lang.select_title")
await message.answer(text, reply_markup=get_language_keyboard("ru"))
```
**Проблема:** Даже для существующих пользователей, которые уже выбрали язык, при /start всегда показывается экран выбора языка. Это может раздражать.
**Рекомендация:** Для существующих пользователей показывать главное меню, выбор языка — только для новых.

### [L-CRIT-5] get_referral_analytics — conn используется вне async with (RUNTIME CRASH)
**Файл:** `database/admin.py:2031-2064`
**Проблема:** Из-за ошибки индентации переменная `conn` используется за пределами `async with pool.acquire() as conn:`. Соединение уже закрыто. Вызов `get_referral_analytics` гарантированно упадёт с `NameError` или ошибкой закрытого соединения.
**Рекомендация:** Исправить индентацию — вернуть строки 2038-2064 внутрь блока `async with`.

### [L-CRIT-6] pending_purchases CHECK constraint блокирует бизнес-тарифы
**Файл:** `database/core.py:487`
```sql
CHECK (tariff IN ('basic', 'plus'))
```
**Проблема:** Таблица `pending_purchases` имеет CHECK constraint, допускающий только 'basic' и 'plus'. Бизнес-тарифы (biz_starter, biz_team, и т.д.) не могут быть сохранены в pending_purchases. Покупка бизнес-тарифа упадёт с SQL ошибкой.
**Рекомендация:** Обновить CHECK constraint: `CHECK (tariff IN ('basic', 'plus', 'biz_starter', 'biz_team', 'biz_business', 'biz_pro', 'biz_enterprise', 'biz_ultimate'))` или использовать `config.VALID_SUBSCRIPTION_TYPES`.

### [L-CRIT-7] Float-to-kopeck конвертация теряет точность
**Файл:** `app/services/subscriptions/service.py:524`
```python
price_kopecks=int(amount_rubles * 100)
```
**Проблема:** IEEE 754 floating point: `int(0.29 * 100)` = `28` вместо `29`. Для некоторых сумм пользователь недоплатит 1 копейку, что может сломать amount validation.
**Рекомендация:** Использовать `round(amount_rubles * 100)` или работать исключительно в копейках (int) по всей цепочке.

### [L-MED-6] VPN client extend_user — telegram_id=0 пропускает DB update
**Файл:** `app/services/vpn_client.py:157-158`
**Проблема:** `if telegram_id:` falsy-check. `telegram_id=0` пройдёт как False, DB update будет пропущен, VPN обновится без синхронизации с БД.
**Рекомендация:** Использовать `if telegram_id is not None:`.

### [L-MED-7] Referral loop detection — только один уровень глубины
**Файл:** `app/services/referrals/service.py:133-144`
**Проблема:** Проверка цикла рефералов только на один уровень (A→B, B→A). Цепочка A→B→C→A не детектируется.
**Рекомендация:** Рекурсивная проверка до 5 уровней или SQL CTE с ограничением глубины.

### [L-MED-8] Дублирование DB запроса в referral service
**Файл:** `app/services/referrals/service.py:116,132`
**Проблема:** `referrer_user` получается дважды с одним и тем же ID — на строке 116 и 132. Избыточный DB-запрос.
**Рекомендация:** Использовать результат первого запроса.

### [L-MED-5] Reminders worker проверяет подписки каждые 45 минут
**Файл:** `reminders.py:213`
```python
await asyncio.sleep(45 * 60)  # 45 минут
```
**Проблема:** При tolerance 30 минут для 3-часового reminder (`timedelta(hours=0.5)`), окно — 1 час. Интервал 45 минут может пропустить напоминание, если подписка попала в окно между двумя проверками.
**Рекомендация:** Уменьшить интервал до 15-20 минут или расширить tolerance.

---

# ЧАСТЬ 3. ПРОИЗВОДИТЕЛЬНОСТЬ

### [P-MED-1] get_all_users_for_export загружает всех пользователей в память
**Файл:** `database/admin.py:66-68`
```python
rows = await conn.fetch("SELECT * FROM users ORDER BY created_at DESC")
return [dict(row) for row in rows]
```
**Проблема:** При большом количестве пользователей (10k+) может занять значительную память.
**Рекомендация:** Использовать server-side cursor или пагинацию.

### [P-MED-2] get_business_metrics — тяжёлые JOIN и regex в SQL
**Файл:** `database/admin.py:168-181`
**Проблема:** `SUBSTRING(al.details FROM 'Payment ID: ([0-9]+)')` — regex в SQL для каждой строки audit_log. При большом audit_log это будет медленно.
**Рекомендация:** Добавить колонку `payment_id` в `audit_log` вместо парсинга из `details`.

### [P-MED-3] fast_expiry_cleanup — N+1 запрос для каждой подписки
**Файл:** `fast_expiry_cleanup.py:199-200`
**Проблема:** Для каждой истёкшей подписки делается отдельный запрос `get_active_paid_subscription`. При 100 подписок — 100 дополнительных запросов.
**Рекомендация:** Включить проверку в основной SQL-запрос через LEFT JOIN.

### [P-LOW-1] Cooperative yield каждые 50 итераций
**Файл:** `auto_renewal.py:146`, `activation_worker.py:136`
**Проблема:** `cooperative_yield()` вызывается каждые 50 итераций, но `BATCH_SIZE = 100`. При 100 записях будет только 1 yield.
**Рекомендация:** Вызывать yield каждые 10-20 записей для лучшей кооперативности.

---

# ЧАСТЬ 4. WORKERS И ФОНОВЫЕ ЗАДАЧИ

### [W-1] Все workers стартуют почти одновременно
**Файл:** `main.py:238-471`
**Проблема:** `reminders`, `trial_notifications`, `farm_notifications`, `healthcheck`, `fast_expiry_cleanup`, `auto_renewal`, `activation_worker`, `xray_sync` — все создаются почти одновременно. При старте все делают первый DB-запрос одновременно, создавая burst.
**Статус:** Частично решено через startup jitter в `auto_renewal`, `activation_worker`, `fast_expiry_cleanup`. Но `reminders` делает `asyncio.sleep(60)`, а не random jitter.
**Рекомендация:** Унифицировать startup jitter для всех workers.

### [W-2] Worker iteration logging дублируется
**Файл:** `activation_worker.py:384-406`
**Проблема:** При `feature_flags_disabled` или `DB_not_ready` — `log_worker_iteration_end` вызывается внутри `if` и затем ещё раз в `finally`. Двойной лог для одной итерации.
**Рекомендация:** Убрать `log_worker_iteration_end` из early-return веток, оставить только в `finally`.

### [W-3] MINIMUM_SAFE_SLEEP_ON_FAILURE различается между workers
- `auto_renewal.py:49` — 300 секунд (5 минут)
- `activation_worker.py:356` — 10 секунд
- `fast_expiry_cleanup.py:46` — 10 секунд

**Проблема:** Несогласованность. auto_renewal спит 5 минут при ошибке, а activation_worker — только 10 секунд.
**Рекомендация:** Вынести в общий конфиг или использовать экспоненциальный backoff.

---

# ЧАСТЬ 5. КАРТА УВЕДОМЛЕНИЙ (NOTIFICATION MAP)

## 5.1 Уведомления пользователям

| Событие | Файл | Текст / i18n ключ | Клавиатура | Язык |
|---------|------|--------------------|------------|------|
| Подписка активирована (webhook) | `confirmation.py:179-186` | `payment.success` (i18n) + fallback RU | `get_connect_keyboard()` | Локализован |
| Баланс пополнен (webhook) | `confirmation.py:155` | `main.balance_topup_success` (i18n) | Нет | Локализован |
| Автопродление успешно | `auto_renewal.py:337-342` | Хардкод RU: "✅ Подписка продлена..." | `get_connect_keyboard()` | **НЕ локализован** |
| VPN активация отложенная | `activation_worker.py:211-215` | Хардкод RU: "🎉 Добро пожаловать..." | `get_connect_keyboard()` | **НЕ локализован** |
| Напоминание 3 дня | `reminders.py:114` | `reminder.paid_3d` (i18n) | `get_renewal_keyboard()` | Локализован |
| Напоминание 24 часа | `reminders.py:119` | `reminder.paid_24h` (i18n) | `get_renewal_keyboard()` | Локализован |
| Напоминание 3 часа | `reminders.py:125` | `reminder.paid_3h` (i18n) | `get_renewal_keyboard()` | Локализован |
| Admin grant 1 день (6ч) | `reminders.py:104` | `reminder.admin_1day_6h` (i18n) | `get_subscription_keyboard()` | Локализован |
| Admin grant 7 дней (24ч) | `reminders.py:109` | `reminder.admin_7days_24h` (i18n) | `get_tariff_1_month_keyboard()` | Локализован |
| Реферал зарегистрирован | `start.py:179-182` | `referral.registered_title` + `registered_date` + `first_payment_notification` (i18n) | Нет | Локализован |
| Gift активирован (новый user) | `start.py:118-127` | `gift.activated_welcome` (i18n) | `get_language_keyboard()` | Локализован |
| Gift активирован (existing user) | `start.py:130-136` | `gift.activated` (i18n) | `get_main_menu_keyboard()` | Локализован |
| Admin: доступ выдан | `admin/access.py` | `admin.grant_user_notification` (i18n) | Нет | Локализован* |
| Admin: доступ отозван | `admin/access.py` | `admin.revoke_user_notification` (i18n) | Нет | Локализован* |
| Admin: ключ перевыпущен | `admin/reissue.py` | `admin.reissue_user_notification` (i18n) | Нет | Локализован* |
| Admin: баланс списан | `admin/finance.py` | `admin.debit_user_notification` (i18n) | Нет | Локализован* |

*\* Admin notifications используют язык пользователя-получателя, что корректно.*

## 5.2 Уведомления администратору

| Событие | Файл | Текст / i18n ключ |
|---------|------|--------------------|
| Деградированный режим | `admin_notifications.py` | `admin.degraded_mode` (i18n) |
| Восстановление БД | `admin_notifications.py` | `admin.recovered` (i18n) |
| Ошибка активации VPN | `activation_worker.py:278-284` | `admin.activation_error_*` (i18n) |
| Pending activations | `activation_worker.py:121-125` | `admin.pending_activations_*` (i18n) |

---

# ЧАСТЬ 6. АРХИТЕКТУРА И ЧИСТОТА КОДА

### [A-1] database/core.py — монолитный файл (55KB+)
**Проблема:** Один файл содержит pool management, helpers, payment operations, VPN lifecycle audit, migration runner. Сложно поддерживать.
**Рекомендация:** Уже частично разделён на `core.py`, `users.py`, `subscriptions.py`, `admin.py`. Продолжить выделение payment/audit операций.

### [A-2] Дублирование VPN API status checks
**Проблема:** Проверка `config.VPN_ENABLED` и `database.DB_READY` дублируется в каждом worker отдельно. Нет единого guard.
**Рекомендация:** Создать декоратор `@requires_db_and_vpn` для workers.

### [A-3] Смешение language hardcode и i18n
**Проблема:** В некоторых местах тексты локализованы через i18n, а в других — захардкожены на русском (auto_renewal, activation_worker).
**Рекомендация:** Полностью перейти на i18n для всех user-facing текстов.

### [A-4] Import внутри функций
**Файлы:** `auto_renewal.py:343`, `activation_worker.py:198`, `main.py:96,103,119`
**Проблема:** Много lazy imports внутри функций. Это допустимо для избежания circular imports, но затрудняет чтение.
**Рекомендация:** Оставить как есть — circular imports реальная проблема в этом проекте.

### [A-5] confirmation.py нарушает архитектуру service layer
**Файл:** `app/services/payments/confirmation.py:13,188`
**Проблема:** Service layer импортирует `from aiogram import Bot` и `from app.handlers.common.keyboards`. Все другие сервисы декларируют "No aiogram imports", этот — нарушает.
**Рекомендация:** Вынести отправку уведомлений из confirmation.py в handler layer. Сервис должен возвращать результат, а handler — отправлять сообщение.

### [A-6] PaymentFinalizationError дублируется в двух модулях
**Файлы:** `app/services/payments/exceptions.py`, `app/services/subscriptions/exceptions.py`
**Проблема:** Один и тот же exception class определён в двух модулях. Если один бросается, а другой ловится — exception не поймается.
**Рекомендация:** Определить в одном месте, реэкспортировать из другого.

### [A-7] Dead code — неиспользуемые exception classes
**Файлы:** Разные
- `ActivationMaxAttemptsReachedError` — определён, но нигде не поднимается
- `PaymentAlreadyProcessedError` — определён, но нигде не поднимается
- `ReminderType.REMINDER_6H` — определён, но не используется в `should_send_reminder()`
**Рекомендация:** Удалить или начать использовать.

### [A-8] Language resolution — нет кэширования
**Файл:** `app/services/language_service.py:26`
**Проблема:** `resolve_user_language()` вызывает `database.get_user()` на каждый вызов. При 10 вызовах в одном handler flow — 10 DB запросов для одного и того же telegram_id.
**Рекомендация:** Добавить in-memory кэш с TTL 60 секунд или передавать language через middleware.

### [A-9] Dual schema management — DDL в init_db + migration files
**Файл:** `database/core.py:465-487`, `migrations/*.sql`
**Проблема:** Схема БД описана в двух местах: `CREATE TABLE IF NOT EXISTS` в `init_db()` и SQL-миграции в папке `migrations/`. Если миграция добавляет колонку, которая уже определена в `init_db`, возникает schema drift.
**Рекомендация:** Убрать DDL из `init_db()`, оставить только миграции как single source of truth.

### [A-10] Миграции без блокировки от конкурентного запуска
**Файл:** `migrations.py:110`
**Проблема:** Нет advisory lock вокруг выполнения миграций. Два инстанса при одновременном старте могут попытаться применить одну миграцию параллельно.
**Рекомендация:** Добавить `pg_advisory_lock` перед выполнением миграций.

### [A-11] decrease_balance логирует refund как "topup"
**Файл:** `database/users.py:354`
**Проблема:** Когда `source="refund"`, `transaction_type` устанавливается в `"topup"`. Возврат средств логируется как пополнение — семантически неверно.
**Рекомендация:** Добавить отдельный `transaction_type = "refund"`.

### [A-12] Admin overview — 6 последовательных DB запросов
**Файл:** `app/services/admin/service.py:60-112`
**Проблема:** `get_admin_user_overview()` делает 6 последовательных DB вызовов: `get_user`, `get_subscription`, `get_user_extended_stats`, `get_user_discount`, `is_vip_user`, `is_trial_available`.
**Рекомендация:** Использовать `asyncio.gather()` для параллельного выполнения.

---

# ДОПОЛНЕНИЕ: HANDLERS, NAVIGATION, PAYMENTS CALLBACKS

## Handlers — game.py

### [L-CRIT-8] Весь game.py (Ферма) — текст захардкожен на русском
**Файлы:** `app/handlers/game.py:30-36, 86-104, 547-570, 584-636`
**Проблема:** PLANT_TYPES (названия растений), текст игрового меню, _render_farm (грядки), все кнопки фермы, все callback.answer() — всё на русском, i18n не используется, хотя `language` доступен.
**Масштаб:** ~30+ hardcoded строк. Пользователи на EN/UZ/DE/AR увидят русский текст.
**Рекомендация:** Создать i18n ключи для всех текстов фермы и игр. Минимально — использовать `i18n_get_text()` для всех user-facing строк.

### [L-MED-9] Farm (game_farm) — нет проверки подписки
**Файл:** `app/handlers/game.py:646-664`
**Проблема:** `games_menu` (line 83-90) проверяет подписку, но `game_farm` — нет. Пользователь может перейти напрямую по callback `game_farm` без подписки.
**Рекомендация:** Добавить проверку подписки в `callback_game_farm`, как в `callback_game_bowling`.

### [L-MED-10] Farm — race condition при параллельных кликах
**Файл:** `app/handlers/game.py:722-752` (и аналогичные: water, fert, harvest, buy_plot)
**Проблема:** Между `get_farm_data()` и `save_farm_plots()` нет блокировки. Два быстрых клика могут прочитать одно состояние и перезаписать друг друга (double harvest, double water).
**Рекомендация:** Использовать `FOR UPDATE` или advisory lock по telegram_id для farm операций.

### [S-MED-10] Hardcoded telegra.ph URL
**Файл:** `app/handlers/game.py:634`
```python
url="https://telegra.ph/Instrukciya-Ferma-02-20"
```
**Проблема:** Захардкоженная внешняя ссылка. Если страница будет удалена — битая ссылка.
**Рекомендация:** Вынести в config или i18n.

## Navigation callbacks — navigation.py

### [L-MED-11] Hardcoded Russian тексты в navigation callbacks
**Файлы:** `app/handlers/callbacks/navigation.py:102, 152, 167, 169, 180, 182, 304`
**Проблема:** Множество user-facing строк захардкожены на русском:
- `"✍️ Трекер Only"` (line 102) — кнопка в экосистеме
- `"🔗 Ваша ссылка подключения готова."` (line 152)
- `"Скопируйте ссылку выше"` / `"Ключ не найден"` (lines 167, 169, 180, 182)
- `"🚀 Нажмите кнопку ниже чтобы подключиться:"` (line 304)
**Рекомендация:** Перевести на i18n ключи.

### [A-13] Неиспользуемая переменная в error handler
**Файл:** `app/handlers/callbacks/navigation.py:285`
```python
user = await database.get_user(telegram_id)
```
**Проблема:** Результат `get_user()` не используется — лишний DB-запрос в обработчике ошибок.
**Рекомендация:** Удалить строку.

### [A-14] Дублирование route для go_profile
**Файл:** `app/handlers/callbacks/navigation.py:255-256`
```python
@router.callback_query(F.data == "go_profile", StateFilter(default_state))
@router.callback_query(F.data == "go_profile")
```
**Проблема:** Второй декоратор перехватывает все состояния, делая первый (с `default_state`) избыточным.
**Рекомендация:** Оставить только один декоратор без StateFilter.

## Payments callbacks — payments_callbacks.py

### [L-CRIT-9] Business welcome text захардкожен на русском
**Файл:** `app/handlers/callbacks/payments_callbacks.py:645`
```python
text = f"🎉 Добро пожаловать в Atlas Secure!\n🏢 Тариф: Business\n📅 До: {expires_str}"
```
**Проблема:** Для бизнес-тарифов текст успешной оплаты полностью на русском, хотя для Basic/Plus используется i18n.
**Рекомендация:** Создать i18n ключ `payment.success_welcome_business`.

### [L-CRIT-10] Upgrade text захардкожен на русском
**Файл:** `app/handlers/callbacks/payments_callbacks.py:616-621`
```python
text = (
    f"⭐️ Апгрейд до Plus!\n"
    f"📅 До: {expires_str}\n\n"
    f"📲 Чтобы конфигурации обновились в приложении:\n"
    f"V2rayTUN — нажмите 🔄 (обновить подписку)"
)
```
**Проблема:** Текст апгрейда захардкожен на русском.
**Рекомендация:** Создать i18n ключ `payment.upgrade_success`.

### [L-MED-12] int(amount * 100) — повторение IEEE 754 бага
**Файл:** `app/handlers/callbacks/payments_callbacks.py:253`
```python
amount_kopecks = int(amount * 100)
```
**Проблема:** Та же проблема float-to-int, что и [L-CRIT-7]. `int(2.29 * 100)` = `228`.
**Рекомендация:** Использовать `round(amount * 100)`.

### [L-MED-13] Withdraw admin notification — text захардкожен на русском
**Файл:** `app/handlers/callbacks/payments_callbacks.py:277-284`
**Проблема:** Текст уведомления админу о заявке на вывод захардкожен на русском. Кнопки "✅ Подтвердить" / "❌ Отклонить" тоже.
**Рекомендация:** Для admin-facing текстов это допустимо (админ один, язык фиксирован). Но callback.answer() типа "Доступ запрещён", "Заявка уже обработана" (lines 317, 323, 333, 336, 339, 352, 362, 365, 368) — это admin-facing, оставить.

### [P-MED-4] show_profile — check_subscription_expiry вызывается дважды
**Файл:** `app/handlers/common/screens.py:218, 222`
```python
await check_subscription_expiry_service(telegram_id)  # line 218
...
await check_subscription_expiry_service(telegram_id)  # line 222
```
**Проблема:** Одна и та же проверка выполняется дважды за один вызов `show_profile()`. Лишний DB-запрос.
**Рекомендация:** Удалить один из вызовов.

### [L-MED-14] format_date_ru используется для всех языков
**Файл:** `app/handlers/common/screens.py:267, 293`
**Проблема:** `format_date_ru(expires_at)` форматирует дату в русском формате ("15 марта 2026") для всех пользователей, включая EN/DE/AR.
**Рекомендация:** Создать `format_date(expires_at, language)` с i18n-совместимым форматированием.

### [L-MED-15] Tariff screen title hardcoded
**Файл:** `app/handlers/common/screens.py:367-368`
```python
text = (
    f"💎 Тарифы Atlas Secure\n\n"
    ...
)
```
**Проблема:** Заголовок экрана тарифов на русском, хотя остальные тексты используют i18n.
**Рекомендация:** Использовать i18n ключ `buy.tariffs_title`.

### [A-15] _REISSUE_LOCKS — растёт неограниченно
**Файл:** `app/handlers/common/utils.py:718-724`
**Проблема:** Dict `_REISSUE_LOCKS` создаёт asyncio.Lock для каждого user_id и никогда не очищается. Аналогично [S-MED-3] (rate limiter buckets).
**Рекомендация:** Добавить TTL-based cleanup или использовать `weakref`.

### [S-MED-11] withdraw_start — обратитесь в поддержку без i18n
**Файл:** `app/handlers/callbacks/payments_callbacks.py:220-223`
```python
await callback.answer(
    "Обратитесь в техподдержку для создания заявки на вывод средств.",
    show_alert=True,
)
```
**Проблема:** User-facing текст без i18n.
**Рекомендация:** Использовать i18n ключ.

---

# ЧАСТЬ 7. ПРЕДЛОЖЕНИЯ ПО ТЕКСТАМ

> **Формат:** Оригинал → Предложение 1 → Предложение 2

### 7.1 Незлокализованные тексты (КРИТИЧНО — нужны i18n ключи)

**auto_renewal.py:337-342**
- **Оригинал:** `"✅ Подписка продлена\n📦/⭐️ Тариф: {tariff_label}\n📅 До: {expires_str}"`
- **Предложение 1:** Создать i18n ключ `auto_renewal.success` = `"✅ Подписка автоматически продлена\n{tariff_icon} Тариф: {tariff}\n📅 Активна до: {date}"` — добавлено слово "автоматически" для ясности
- **Предложение 2:** `"✅ Автопродление выполнено\n{tariff_icon} {tariff}\n📅 До: {date}\n💳 Списано: {amount} ₽"` — добавить сумму списания для прозрачности

**activation_worker.py:211-215**
- **Оригинал:** `"🎉 Добро пожаловать в Atlas Secure!\n{tariff_emoji} Тариф: {tariff_label}\n📅 До: {expires_str}"`
- **Предложение 1:** Создать i18n ключ `activation.vpn_ready` = `"🎉 VPN подключение готово!\n{tariff_icon} Тариф: {tariff}\n📅 Активно до: {date}"` — более точное описание события
- **Предложение 2:** `"✅ Подписка активирована!\n{tariff_icon} {tariff}\n📅 До: {date}\n\nНажмите «Подключиться» для настройки VPN"` — call to action

**payments_callbacks.py:645 (Business welcome)**
- **Оригинал:** `"🎉 Добро пожаловать в Atlas Secure!\n🏢 Тариф: Business\n📅 До: {expires_str}"`
- **Предложение 1:** Создать i18n ключ `payment.success_welcome_business` = `"🎉 Добро пожаловать в Atlas Secure!\n🏢 Тариф: {tariff}\n📅 Активно до: {date}"` — унифицировать с Basic/Plus
- **Предложение 2:** `"🏢 Бизнес-подписка активирована!\n📅 До: {date}\n\n🎛 Управление: /profile"` — с CTA

**payments_callbacks.py:616-621 (Upgrade to Plus)**
- **Оригинал:** `"⭐️ Апгрейд до Plus!\n📅 До: {expires_str}\n\n📲 Чтобы конфигурации обновились в приложении:\nV2rayTUN — нажмите 🔄 (обновить подписку)"`
- **Предложение 1:** Создать i18n ключ `payment.upgrade_success` = `"⭐️ Апгрейд до Plus!\n📅 До: {date}\n\n📲 Обновите конфигурацию в VPN-приложении"` — короче и понятнее
- **Предложение 2:** `"⭐️ Тариф повышен до Plus!\n📅 Активно до: {date}\n\nНажмите «Подключиться» для обновления"` — с кнопкой connect

**navigation.py:304 (Connect instead of copy)**
- **Оригинал:** `"🚀 Нажмите кнопку ниже чтобы подключиться:"`
- **Предложение 1:** Создать i18n ключ `vpn.connect_prompt` = `"🚀 Подключитесь через кнопку ниже:"`
- **Предложение 2:** `"🚀 Нажмите «Подключиться» для настройки VPN"`

**game.py:86-104 (Games menu)**
- **Оригинал:** Полный русский текст игрового меню (18 строк)
- **Предложение 1:** Создать i18n ключ `games.welcome` со всем текстом меню
- **Предложение 2:** Сократить текст и сделать его более dynamic: показывать только доступные игры с краткими описаниями

### 7.2 Уведомления admin

**ru.py:91 — admin.grant_user_notification**
- **Оригинал:** `"✅ Вам предоставлен доступ к Atlas Secure на {days} дней.\nVPN-ключ: {vpn_key}\nСрок действия: до {date}"`
- **Предложение 1:** `"✅ Вам предоставлен доступ к Atlas Secure\n\n📅 Срок: {days} дн.\n📅 До: {date}\n\nНажмите «Подключиться» для настройки"` — убрать VPN-ключ из текста (пользователь нажмёт кнопку)
- **Предложение 2:** Оставить как есть, но добавить кнопку `get_connect_keyboard()` в хэндлер

**ru.py:92 — admin.grant_user_notification_10m**
- **Оригинал:** `"⏱ Доступ активирован на 10 минут.\n\nВы можете подключиться сразу.\nПо окончании доступ будет приостановлен автоматически."`
- **Предложение 1:** `"⏱ Тестовый доступ — 10 минут\n\nПодключитесь прямо сейчас!\nДоступ завершится автоматически."` — короче и динамичнее
- **Предложение 2:** `"⏱ Доступ на 10 мин.\n\nНажмите «Подключиться».\nПо истечении времени доступ отключится автоматически."` — с CTA

**ru.py:149 — admin.reissue_user_notification**
- **Оригинал:** `"🔐 Обновление VPN-ключа\n\nВаш VPN-ключ обновлён администратором\nи переведён на новую версию сервера.\n\nДля корректной работы:\n— удалите старый ключ из VPN-приложения\n— добавьте новый ключ доступа\n\nКлюч:\n\n{vpn_key}\n\nОбновление необходимо для сохранения\nстабильности и производительности соединения."`
- **Предложение 1:** `"🔐 VPN-ключ обновлён\n\nМы обновили ваш ключ для улучшения соединения.\n\n📋 Инструкция:\n1. Удалите старый ключ из VPN-приложения\n2. Нажмите «Подключиться» для добавления нового\n\nЭто необходимо для стабильной работы."` — убрать ключ из текста, добавить кнопку
- **Предложение 2:** Оставить текст, но сократить: `"🔐 VPN-ключ обновлён\n\nУдалите старый ключ и добавьте новый:\n\n{vpn_key}\n\n(Обновление для стабильности соединения)"` — компактнее

### 7.3 Rate limit message

**rate_limit.py:185**
- **Оригинал:** `"Слишком много запросов. Попробуйте через {wait_seconds} секунд."`
- **Предложение 1:** Создать i18n ключ `common.rate_limit` = `"⏳ Подождите {seconds} сек. Слишком частые запросы."` — добавить эмодзи и перефразировать
- **Предложение 2:** `"⏳ Слишком много запросов. Повторите через {seconds} сек."` — компактнее

### 7.4 Emoji в tariff label

**auto_renewal.py:337**
- **Оригинал:** `"📦/⭐️"` — оба эмодзи в тексте
- **Предложение 1:** Использовать условный emoji: `"📦"` для Basic, `"⭐️"` для Plus — как уже сделано в `activation_worker.py:210`
- **Предложение 2:** Использовать `tariff_emoji` переменную, сгенерированную в том же блоке кода

### 7.5 Экран /start — всегда показывает выбор языка

**start.py:206-207**
- **Текущее поведение:** Всегда показывает `lang.select_title` с клавиатурой выбора языка
- **Предложение 1:** Для существующих пользователей показывать `main.welcome` + `get_main_menu_keyboard()`, выбор языка только для новых
- **Предложение 2:** Оставить как есть для unification flow, но добавить кнопку "Продолжить на {текущем языке}" первой в списке

---

# ЧАСТЬ 8. РЕЗЮМЕ

## Статистика найденных проблем

| Категория | Критические | Средние | Низкие |
|-----------|-------------|---------|--------|
| Безопасность | 3 | 11 | 2 |
| Корректность логики | 10 | 15 | 0 |
| Производительность | 0 | 4 | 1 |
| Workers | 0 | 3 | 0 |
| Архитектура | 0 | 15 | 0 |
| **Итого** | **13** | **48** | **3** |

## Приоритеты исправления

### P0 (Немедленно — может ломать production):
1. [L-CRIT-5] — `get_referral_analytics` crash — conn вне async with
2. [L-CRIT-6] — pending_purchases CHECK блокирует бизнес-тарифы
3. [S-CRIT-2] — Webhook 200 при ошибке может терять платежи
4. [L-CRIT-7] + [L-MED-12] — Float-to-kopeck потеря точности (финансовая ошибка) — 2 места
5. [L-CRIT-3] — Нелокализованные уведомления автопродления
6. [L-CRIT-4] — Нелокализованные уведомления активации
7. [L-CRIT-8] — Весь game.py/Farm — тексты захардкожены на русском (~30+ строк)
8. [L-CRIT-9] — Business welcome text захардкожен на русском
9. [L-CRIT-10] — Upgrade text захардкожен на русском
10. [L-MED-9] — Farm без проверки подписки (обход paywall)

### P1 (В ближайшие спринты):
1. [S-CRIT-1] — Platega webhook аутентификация
2. [S-CRIT-3] — MINI_APP_URL в config
3. [S-MED-5] — Advisory lock enforcement
4. [S-MED-6] — Health check alert может утечь DB creds
5. [S-MED-8] — Referral код enumerable
6. [L-CRIT-1] — Проверить единицы баланса в auto_renewal
7. [L-CRIT-2] — create_payment всегда 30 дней
8. [L-MED-10] — Farm race condition (double harvest)
9. [L-MED-11] — Navigation callbacks — hardcoded Russian
10. [A-5] — confirmation.py нарушает архитектуру service layer
11. [A-6] — Дублирование PaymentFinalizationError
12. [A-9] — Dual schema management (DDL + migrations)
13. [A-10] — Миграции без блокировки

### P2 (При рефакторинге):
- Все средние проблемы безопасности и корректности
- Производительность export и metrics queries
- Workers consistency
- Dead code cleanup [A-7], [A-13] unused user variable, [A-14] duplicate route
- Language caching [A-8]
- Admin overview parallelization [A-12]
- pytest из production requirements [S-MED-7]
- Refund как topup в логах [A-11]
- [S-MED-10] — Hardcoded telegra.ph URL
- [S-MED-11] — withdraw_start без i18n
