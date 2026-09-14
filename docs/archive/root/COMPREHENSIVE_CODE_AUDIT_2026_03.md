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

# ДОПОЛНЕНИЕ 1: ГЛУБОКИЙ АНАЛИЗ АГЕНТОВ (handlers, middleware, i18n, workers)

## Безопасность — новые критические находки

### [S-CRIT-4] Race condition: webhook heartbeat без синхронизации
**Файл:** `app/api/telegram_webhook.py:52`
**Проблема:** `global last_webhook_update_at` обновляется без lock из конкурентных async-хендлеров. Watchdog может прочитать повреждённый timestamp.
**Рекомендация:** Использовать `threading.Lock()` для atomic update.

### [S-CRIT-5] Race condition: rate limiter shared state
**Файл:** `app/core/rate_limit_middleware.py:137,143,177-178`
**Проблема:** `self._user_requests` и `self._banned_users` (dict) модифицируются конкурентно без синхронизации. Dict операции не atomic. Конкурентные запросы от одного user_id могут повредить структуру данных.
**Рекомендация:** Добавить `asyncio.Lock()` для всех операций с shared dictionaries.

### [S-CRIT-6] Race condition: pool monitor global heartbeat
**Файл:** `app/core/pool_monitor.py:42-44, 52`
**Проблема:** `_last_pool_wait_spike_monotonic` обновляется без блокировки из конкурентных `__aenter__` вызовов.
**Рекомендация:** Обернуть в `threading.Lock()`.

### [S-HIGH-1] Admin auth check — silent failure (authorization bypass potential)
**Файл:** `app/handlers/admin/access.py:454-459`
**Проблема:** Неавторизованные пользователи получают `callback.answer()` без сообщения об ошибке. Позволяет разведку (silent failure выглядит как отсутствие handler'а).
**Рекомендация:** Всегда отправлять явное `access_denied` с `show_alert=True`.

### [S-HIGH-2] Финансовая операция без верификации target user_id
**Файл:** `app/handlers/admin/access.py:816-847`
**Проблема:** `admin_switch_confirm:{tariff}:{user_id}` — user_id извлекается из callback_data без проверки, что он был pre-selected. Replay attack: модифицировать user_id в callback → смена тарифа произвольному пользователю.
**Рекомендация:** Хранить selected user_id в FSM state и верифицировать при confirm.

### [S-HIGH-3] Gift code activation без rate limiting — brute force
**Файл:** `app/handlers/user/start.py:94-125`
**Проблема:** Нет rate limiting на попытки активации gift-кодов. Атакующий может перебирать коды без ограничений. Нет логирования неудачных попыток.
**Рекомендация:** Добавить `check_rate_limit(telegram_id, "gift_activate", limit=5, window=3600)`.

### [S-HIGH-4] Webhook signature verification delegated without assertion
**Файл:** `app/api/payment_webhook.py:78-85`
**Проблема:** CryptoBot webhook handler читает `raw_body` но не валидирует подпись перед JSON парсингом. Верификация делегирована в service layer без явной проверки результата.
**Рекомендация:** Добавить explicit signature verification в handler до обработки.

### [S-MED-12] Callback data parsing без bounds check (IndexError)
**Файл:** `app/handlers/callbacks/payments_callbacks.py:108-114`
```python
amount_str = callback.data.split(":")[1]  # IndexError если ":"  отсутствует
```
**Проблема:** Если callback_data = `"topup_amount"` (без `:`) — `split(":")[1]` бросит IndexError. Aiogram filter `startswith("topup_amount:")` требует `:`, но callback может быть подменён.
**Аналогично:** `payments/callbacks.py:983` (country_code), множество admin handlers.
**Рекомендация:** Проверять `len(parts) >= 2` перед доступом к `parts[1]`.

### [S-MED-13] Missing DB readiness check в admin reissue
**Файл:** `app/handlers/admin/reissue.py:30-55`
**Проблема:** `/reissue_key` command не вызывает `ensure_db_ready_message()`. Если БД не инициализирована — crash.
**Рекомендация:** Добавить guard в начало handler'а.

### [S-MED-14] safe_send_message без timeout
**Файл:** `app/utils/telegram_safe.py:24`
**Проблема:** `bot.send_message()` вызывается без timeout. Если Telegram API зависнет — блокируется весь event loop.
**Рекомендация:** Обернуть в `asyncio.wait_for(..., timeout=10.0)`.

### [S-MED-15] Health endpoint без аутентификации
**Файл:** `app/api/__init__.py:41`
**Проблема:** `/health` endpoint открыт публично, экспонирует состояние системы (DB, Redis).
**Рекомендация:** Возвращать минимум (`{"status":"ok"}`) или добавить IP rate limiting.

## Корректность — новые находки

### [L-CRIT-11] Farm notifications — все тексты захардкожены на русском
**Файл:** `app/workers/farm_notifications.py:71-72, 85-86, 100-101`
**Проблема:** Уведомления о созревании, предупреждении (12ч) и гибели растений — полностью на русском. i18n не используется, хотя `language` доступен (но не получается — нет вызова resolve_user_language).
**Масштаб:** 3 notification templates + plant names dict (lines 25-30).
**Рекомендация:** Добавить `resolve_user_language(telegram_id)` и i18n ключи для всех farm notifications.

### [L-CRIT-12] Format string placeholder mismatch: games.dice_success
**Файл:** `app/i18n/en.py` vs `app/i18n/ru.py`
**Проблема:** EN версия содержит только `{value}`, RU версия — `{value}` и `{date}`. Если код вызывает `i18n.get_text("ru", "games.dice_success", value=100)` — `KeyError: 'date'` crash.
**Рекомендация:** Синхронизировать placeholders между языками.

### [L-MED-16] Race condition в toggle_auto_renew
**Файл:** `app/handlers/callbacks/subscription.py:45-73`
**Проблема:** UPDATE без проверки результата (0 rows). Нет transaction isolation. Два быстрых клика могут конфликтовать.
**Рекомендация:** Проверять результат UPDATE, использовать `WHERE status = 'active'`.

### [L-MED-17] Missing RU translation keys (5 ключей)
**Файл:** `app/i18n/ru.py`
**Ключи:** `payment.success_welcome_basic`, `payment.success_welcome_plus`, `referral.cashback_referred`, `referral.registered_user`, `referral.trial_activated_user`
**Проблема:** Существуют в EN, отсутствуют в RU. Русские пользователи увидят EN fallback.
**Рекомендация:** Добавить перевод этих 5 ключей в ru.py.

### [L-MED-18] AR (Arabic) — те же 5 ключей отсутствуют
**Файл:** `app/i18n/ar.py`
**Проблема:** Аналогично RU — те же 5 ключей отсутствуют.
**Рекомендация:** Добавить арабский перевод.

## Производительность — новые находки

### [P-MED-5] N+1 query в bulk key reissue
**Файл:** `app/handlers/admin/access.py:119-183`
**Проблема:** Для каждой подписки из списка вызывается `reissue_vpn_key_atomic()` (3-5 DB операций). При 1000 подписок = 3000-5000 queries.
**Рекомендация:** Использовать batch operations.

### [P-MED-6] Blocking dictionary cleanup в async middleware
**Файл:** `app/core/rate_limit_middleware.py:145-156`
**Проблема:** Синхронная очистка до 25,000 записей блокирует event loop.
**Рекомендация:** Рефакторить в async метод с yield через `asyncio.sleep(0)`.

## Workers — новые находки

### [W-4] Farm notifications worker — нет startup jitter
**Файл:** `app/workers/farm_notifications.py:114`
**Проблема:** Фиксированный `asyncio.sleep(60)` без random jitter. При одновременном рестарте контейнеров — burst DB запросов.
**Рекомендация:** Добавить `jitter_s = random.uniform(5, 60)` как в других workers.

## Архитектура — новые находки

### [A-16] Audit log failure cascade — silent loss
**Файл:** `app/utils/audit.py:227-247`
**Проблема:** Если запись audit log и fallback `log_security_error()` оба падают — событие потеряно без следа.
**Рекомендация:** Добавить last-resort fallback на stderr.

### [A-17] Error disclosure в admin responses
**Файл:** `app/handlers/admin/access.py:254-259`
**Проблема:** Exception message (обрезанная до 80 символов) отправляется админу. Может содержать DB credentials, API endpoints.
**Рекомендация:** Отправлять generic message, логировать полную ошибку.

### [A-18] Inconsistent logging — JSON vs string
**Файл:** `app/utils/logging_helpers.py:111, 165-171`
**Проблема:** Часть логов в JSON, часть — в произвольном формате. Затрудняет агрегацию.
**Рекомендация:** Стандартизировать формат логирования.

---

# ДОПОЛНЕНИЕ 2: HANDLERS, NAVIGATION, PAYMENTS CALLBACKS

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
| Безопасность | 6 | 15 | 2 |
| Безопасность (HIGH) | 4 | — | — |
| Корректность логики | 12 | 18 | 0 |
| Производительность | 0 | 6 | 1 |
| Workers | 0 | 4 | 0 |
| Архитектура | 0 | 18 | 0 |
| Миграции | 2 | 4 | 0 |
| Тесты | 2 | 1 | 1 |
| Хэндлеры (доп.) | 1 | 2 | 0 |
| Баги/логика (доп.) | 0 | 5 | 0 |
| Интеграции/VPN/скрипты | 2 | 2 | 0 |
| Баги интеграций | 0 | 5 | 0 |
| Middleware/утилиты/core | 2 | 4 | 0 |
| i18n/переводы (доп.) | 3 | 4 | 0 |
| **Итого** | **34** | **84** | **4** |

> Примечание: "Безопасность (HIGH)" — это проблемы высокой серьёзности, не дотягивающие до CRIT, но требующие срочного внимания (admin auth bypass, brute force, missing signature validation).

## Приоритеты исправления

### P0 (Немедленно — может ломать production):
1. [L-CRIT-5] — `get_referral_analytics` crash — conn вне async with
2. [L-CRIT-6] — pending_purchases CHECK блокирует бизнес-тарифы
3. [S-CRIT-2] — Webhook 200 при ошибке может терять платежи
4. [S-CRIT-4/5/6] — Race conditions: webhook heartbeat, rate limiter state, pool monitor
5. [L-CRIT-7] + [L-MED-12] — Float-to-kopeck потеря точности — 2 места
6. [L-CRIT-12] — Format string placeholder mismatch (games.dice_success → crash)
7. [S-HIGH-2] — Admin financial op без верификации target user_id
8. [S-HIGH-4] — Webhook signature не проверяется в handler

### P0.5 (Критично для UX — не ломает, но портит):
9. [L-CRIT-3] — Нелокализованные уведомления автопродления
10. [L-CRIT-4] — Нелокализованные уведомления активации
11. [L-CRIT-8] — game.py/Farm — тексты на русском (~30+ строк)
12. [L-CRIT-9] — Business welcome text на русском
13. [L-CRIT-10] — Upgrade text на русском
14. [L-CRIT-11] — Farm notifications — все на русском (3 templates)
15. [L-MED-9] — Farm без проверки подписки (обход paywall)

### P1 (В ближайшие спринты):
1. [S-CRIT-1] — Platega webhook аутентификация
2. [S-CRIT-3] — MINI_APP_URL в config
3. [S-HIGH-1] — Admin auth check silent failure
4. [S-HIGH-3] — Gift code activation без rate limiting
5. [S-MED-5] — Advisory lock enforcement
6. [S-MED-6] — Health check alert может утечь DB creds
7. [S-MED-8] — Referral код enumerable
8. [S-MED-12] — Callback data parsing без bounds check
9. [S-MED-13] — Missing DB readiness check в admin reissue
10. [S-MED-14] — safe_send_message без timeout
11. [L-CRIT-1] — Проверить единицы баланса в auto_renewal
12. [L-CRIT-2] — create_payment всегда 30 дней
13. [L-MED-10] — Farm race condition (double harvest)
14. [L-MED-17/18] — Missing RU/AR translation keys
15. [A-5] — confirmation.py нарушает service layer
16. [A-9] — Dual schema management
17. [A-10] — Миграции без блокировки

### P2 (При рефакторинге):
- Все оставшиеся средние проблемы безопасности и корректности
- [L-MED-11] — Navigation callbacks hardcoded Russian
- [L-MED-14] — format_date_ru для всех языков
- [L-MED-15] — Tariff screen title hardcoded
- [L-MED-16] — Race condition в toggle_auto_renew
- [P-MED-4] — Дублированный check_subscription_expiry
- [P-MED-5] — N+1 query в bulk reissue
- [P-MED-6] — Blocking cleanup в rate limiter
- [W-4] — Farm notifications worker без jitter
- Dead code: [A-7], [A-13], [A-14]
- Language caching [A-8], Admin parallelization [A-12]
- pytest из production [S-MED-7], Refund как topup [A-11]
- [A-15] — _REISSUE_LOCKS unbounded, [S-MED-10], [S-MED-11]
- [A-16] — Audit failure cascade, [A-17] — Error disclosure, [A-18] — Logging format

---

# ДОПОЛНЕНИЕ 3. МИГРАЦИИ, ТЕСТЫ И СХЕМА БД

## D3.1 МИГРАЦИИ

### [M-CRIT-1] Дублированный номер миграции 006
**Файлы:** `migrations/006_add_subscription_fields.sql`, `migrations/006_broadcast_discounts.sql`
**Проблема:** Два файла с одинаковым номером 006. Порядок выполнения зависит от алфавитной сортировки (`006_add_subscription_fields` → `006_broadcast_discounts`), что ненадёжно. Если runner использует другую стратегию сортировки — одна из миграций может не выполниться или выполниться в неверном порядке.
**Рекомендация:** Переименовать `006_broadcast_discounts.sql` в `006b_broadcast_discounts.sql` или в следующий свободный номер.

### [M-CRIT-2] Пропущенные номера миграций: 011 и 030
**Файлы:** отсутствуют `011_*.sql` и `030_*.sql`
**Проблема:** Последовательность идёт 010 → 012 и 029 → 031. Если migration runner проверяет непрерывность номеров, он может зависнуть или выдать ошибку. Также это затрудняет аудит — неясно, были ли миграции удалены или никогда не существовали.
**Рекомендация:** Задокументировать пропуски в `migrations/README.md` или создать файлы-заглушки (`011_placeholder.sql`, `030_placeholder.sql`) с комментарием "intentionally skipped".

### [M-MED-1] Отсутствие rollback/DOWN секций
**Файлы:** все 34 миграции
**Проблема:** Ни одна миграция не содержит секции DOWN/ROLLBACK. При необходимости откатить изменение схемы — только ручное вмешательство.
**Рекомендация:** Добавить DOWN-секции хотя бы для критических миграций (CREATE TABLE, ALTER TABLE). Для idempotent миграций с `IF NOT EXISTS` это менее критично.

### [M-MED-2] CHECK constraint на tariff блокирует расширение тарифов
**Файл:** `migrations/004_add_pending_purchases.sql:20`, `migrations/017_add_purchase_type_for_balance_topup.sql:30`
```sql
CHECK (tariff IN ('basic', 'plus'))
CHECK (tariff IS NULL OR tariff IN ('basic', 'plus'))
```
**Проблема:** Business-тариф и любой новый тариф не пройдёт CHECK constraint. Это уже отмечено как [L-CRIT-6], но важно что constraint задан в двух разных миграциях — нужно менять оба.
**Связано с:** [L-CRIT-6]

### [M-MED-3] Смешанные типы для денежных значений
**Файлы:** `migrations/001_init.sql`, `migrations/002_add_balance.sql`
**Проблема:** `payments.amount` — `INTEGER`, `users.balance` — `INTEGER`, но `balance_transactions.amount` — `NUMERIC`. Смешение INTEGER (копейки) и NUMERIC (рубли с дробной частью) создаёт путаницу и риск ошибок при расчётах.
**Рекомендация:** Стандартизировать: все суммы хранить в INTEGER (копейки) ИЛИ NUMERIC(12,2) (рубли). Добавить комментарии к столбцам с указанием единиц.

### [M-MED-4] Отсутствие FOREIGN KEY между основными таблицами
**Проблема:** FK определены только для `broadcast_stats → broadcasts` и `broadcast_discounts → broadcasts`. Нет FK:
- `payments.telegram_id → users.telegram_id`
- `subscriptions.telegram_id → users.telegram_id`
- `referrals.referrer_id → users.telegram_id`
- `pending_purchases.telegram_id → users.telegram_id`
**Следствие:** Возможно создание "осиротевших" записей (payments для несуществующего пользователя). Также отсутствие FK лишает БД возможности автоматического каскадного удаления.
**Рекомендация:** Добавить FK с `ON DELETE CASCADE` или `ON DELETE SET NULL` в зависимости от бизнес-логики. Или документировать решение не использовать FK как architectural decision.

## D3.2 ТЕСТЫ

### [T-CRIT-1] No-op тест: TestExpiredSubscriptionRemoved
**Файл:** `tests/integration/test_vpn_entitlement.py:104-146`
**Проблема:** Тест `test_fast_expiry_cleanup_calls_remove_for_expired` устанавливает все моки, но фактическое тело теста — `pass` (строка 143). Финальный assert — `assert True` (строка 146). Тест всегда проходит, ничего не проверяя.
```python
with patch("fast_expiry_cleanup.database._from_db_utc", side_effect=lambda x: x):
    pass  # Structural test; full run would need event loop
assert True
```
**Рекомендация:** Реализовать тест или пометить `@pytest.mark.skip(reason="not implemented")` чтобы не создавать ложное ощущение покрытия.

### [T-CRIT-2] Тесты не запускаются из-за config import
**Проблема:** При запуске `pytest` — crash на `config.py` из-за `PROD_BOT_TOKEN environment variable is not set!`. Модуль `config.py` вызывает `sys.exit(1)` при отсутствии обязательных env vars, что делает невозможным запуск тестов без полного production окружения.
**Рекомендация:**
1. Использовать `conftest.py` с `monkeypatch` для установки env vars до импорта config
2. Или обернуть `sys.exit()` в config.py проверкой `if not os.getenv("TESTING")`
3. Или использовать `.env.test` файл с mock-значениями

### [T-MED-1] Массовые пробелы в тестовом покрытии
**Проблема:** Не существует тестов для:
- Referral system (регистрация рефералов, cashback расчёт, циклические рефералы)
- Promo codes (создание, применение, лимиты использования, сроки)
- Balance operations (пополнение, списание, транзакции)
- Broadcast system (отправка, сегментация, шаблоны)
- VPN key management (создание, перевыпуск, удаление)
- Auto-renewal worker (продление, ошибки, нотификации)
- Farm game (посадка, сбор, гниение, нотификации)
- Games (bowling, dice, bomber — cooldowns, rewards)
- Admin operations (grant access, switch tariff, finance)

Из ~60+ модулей в проекте тесты покрывают только 5 файлов:
- `test_webhook_signatures.py`
- `services/test_admin.py`
- `services/test_payments.py`
- `services/test_subscriptions.py`
- `services/test_trials.py`
- `integration/test_vpn_entitlement.py` (частично no-op)

### [T-LOW-1] Неиспользуемые fixtures в conftest.py
**Файл:** `tests/conftest.py:62-74`
**Проблема:** `mock_database` fixture определён, но не используется ни в одном тесте (тесты напрямую патчат `database` модуль через `unittest.mock.patch`).
**Рекомендация:** Удалить неиспользуемые fixtures или переписать тесты для их использования.

## D3.3 Обновлённые приоритеты

### P0 (добавления):
- [T-CRIT-2] — Исправить запуск тестов (без тестов невозможна CI/CD)

### P1 (добавления):
- [M-CRIT-1] — Дублированный номер миграции 006
- [M-CRIT-2] — Задокументировать пропущенные номера миграций
- [T-CRIT-1] — Исправить или удалить no-op тест

### P2 (добавления):
- [M-MED-1] — Добавить rollback секции
- [M-MED-3] — Стандартизировать типы для денежных столбцов
- [M-MED-4] — Добавить FOREIGN KEY
- [T-MED-1] — Написать тесты для критических бизнес-модулей
- [T-LOW-1] — Очистить conftest.py

---

# ДОПОЛНЕНИЕ 4. ХЭНДЛЕРЫ — РАСШИРЕННЫЙ АНАЛИЗ

## D4.1 БЕЗОПАСНОСТЬ

### [H-CRIT-1] Race condition в балансовой оплате подарка (gift.py)
**Файл:** `app/handlers/gift.py:~254`
**Проблема:** В `callback_gift_pay_balance` баланс проверяется через `get_user_balance`, затем списывается через `decrease_balance` в отдельном вызове. Между проверкой и списанием другой конкурентный запрос может потратить те же средства. FSM state `processing_payment` даёт частичную защиту, но не атомарен с проверкой баланса.
**Рекомендация:** Использовать атомарную DB-операцию `UPDATE users SET balance = balance - $1 WHERE telegram_id = $2 AND balance >= $1 RETURNING balance`.

### [H-MED-1] CSV-экспорт содержит VPN-ключи
**Файл:** `app/handlers/admin/export.py:129`
**Проблема:** CSV-экспорт включает столбец `vpn_key`, отправляемый как документ в Telegram. Файл содержит все активные ключи подписок. Утечка этого файла = компрометация всех пользователей VPN.
**Рекомендация:** Исключить VPN-ключи из экспорта или маскировать (`vless://***...abc`). Или отправлять файл с auto-delete таймером.

### [H-MED-2] Промокод допускает 0% и 100% скидку
**Файл:** `app/handlers/admin/promo_fsm.py:70`
**Проблема:** Валидация `if discount_percent < 0 or discount_percent > 100` — допускает 0% (бесполезный промо) и 100% (полностью бесплатный). Персональная скидка в `finance.py:130` правильно использует 1-99%.
**Рекомендация:** Изменить на `if discount_percent < 1 or discount_percent > 99`.

## D4.2 БАГИ И ЛОГИКА

### [H-BUG-1] Дублированные клавиатуры: common/keyboards.py vs admin/keyboards.py
**Файлы:** `app/handlers/common/keyboards.py`, `app/handlers/admin/keyboards.py`
**Проблема:** 8+ клавиатур определены в обоих файлах с РАЗНЫМИ реализациями:
- `get_admin_dashboard_keyboard`, `get_admin_back_keyboard`, `get_admin_export_keyboard`
- `get_broadcast_test_type_keyboard`, `get_broadcast_segment_keyboard`, `get_broadcast_confirm_keyboard`
- `get_ab_test_list_keyboard`, `get_admin_user_keyboard`
Admin handlers импортируют из `admin/keyboards.py` (более полная версия), остальные — из `common/keyboards.py`. При рефакторинге легко импортировать не ту версию.
**Рекомендация:** Удалить дубли из `common/keyboards.py`, оставить только в `admin/keyboards.py`.

### [H-BUG-2] Broadcast блокирует event loop
**Файл:** `app/handlers/admin/broadcast.py:730`
**Проблема:** `callback_broadcast_confirm_send` блокирует хэндлер до отправки всех сообщений (цикл с `await asyncio.gather`). Для тысяч пользователей хэндлер зависнет на минуты, блокируя обработку других callback-ов.
**Рекомендация:** Обернуть в `asyncio.create_task()` как уже сделано для no-subscription broadcast (line 289).

### [H-BUG-3] Unreachable docstring в /start (start.py:62)
**Файл:** `app/handlers/start.py:62`
**Проблема:** Docstring `"""Обработчик команды /start"""` стоит после executable code (early return на line 61). Строка становится no-op выражением, а не docstring функции.

### [H-BUG-4] Неиспользуемые DB-запросы
- `app/handlers/admin/audit.py:133` — `user = await database.get_user(...)` — результат не используется
- `app/handlers/admin/broadcast.py:295` — `user = await database.get_user(...)` — результат не используется
**Рекомендация:** Удалить ненужные запросы для экономии DB-ресурсов.

### [H-BUG-5] Дублированный audit log formatting (audit.py)
**Файл:** `app/handlers/admin/audit.py:30-237`
**Проблема:** Форматирование audit log дублировано почти идентично между `cmd_admin_audit` (30-127) и `callback_admin_audit` (130-237). Плюс "retry with limit=5" path дублирует ту же логику ещё раз.
**Рекомендация:** Вынести в общую функцию `_format_audit_entries(entries, limit)`.

## D4.3 Обновлённая статистика

Добавлено: 1 CRIT, 2 MED (безопасность), 5 BUG/LOGIC = +8 issues

---

# ДОПОЛНЕНИЕ 5. ВНЕШНИЕ ИНТЕГРАЦИИ, VPN API, СКРИПТЫ

## D5.1 БЕЗОПАСНОСТЬ

### [E-CRIT-1] /self-test endpoint без аутентификации
**Файл:** `xray_api/main.py:437-452`
**Проблема:** Endpoint `/self-test` исключён из API key middleware. Он создаёт и удаляет тестовых пользователей в Xray конфиге без аутентификации. Атакующий, получивший доступ к localhost:8000, может злоупотреблять этим для манипуляции конфигом или рестарт-штормов через очередь мутаций.
**Рекомендация:** Добавить API key проверку для `/self-test` или ограничить частоту вызовов.

### [E-CRIT-2] vpn_server_audit.sh выгружает приватные ключи в /tmp/
**Файл:** `scripts/vpn_server_audit.sh:56-74`
**Проблема:** Скрипт выполняет `cat /etc/wireguard/*.conf`, `cat /etc/xray/config.json` и т.д., выгружая приватные ключи и секреты в stdout и audit log в `/tmp/` — world-readable директорию.
**Рекомендация:** Редактировать приватные ключи при выводе (`PrivateKey: ***`). Писать лог в `/var/log/` с ограниченными правами (600).

### [E-MED-1] IP-валидация в vpn_utils слишком широкая
**Файл:** `vpn_utils.py:91-103`
**Проблема:** `_validate_api_url_security` проверяет подстроки `'172.'`, `'10.'`, `'192.168'` в полном URL. Домен типа `api10.example.com` или `user172.host.com` будет ошибочно заблокирован.
**Рекомендация:** Парсить URL через `urllib.parse`, извлекать hostname и проверять только его.

### [E-MED-2] Webhook amount fallback — финансовый риск
**Файлы:** `cryptobot_service.py:202-204`, `platega_service.py:195-197`
**Проблема:** Если сумма в вебхуке = 0 или отсутствует, код берёт `pending_purchase["price_kopecks"] / 100.0`. Платёж подтверждается по ожидаемой, а не фактической сумме. Если пользователь каким-то образом заплатил меньше — операция всё равно будет подтверждена.
**Рекомендация:** Логировать расхождение и отклонять платёж если фактическая сумма < expected * 0.95.

## D5.2 БАГИ И ЛОГИКА

### [E-BUG-1] auto_renewal UPDATE по telegram_id вместо subscription id
**Файл:** `auto_renewal.py:157-163, 174-179`
**Проблема:** UPDATE и re-check запросы используют `telegram_id` вместо `subscription.id`. Если у пользователя несколько подписок (маловероятно, но возможно) — может обновиться не та подписка.
**Рекомендация:** Использовать `WHERE id = $1` вместо `WHERE telegram_id = $1`.

### [E-BUG-2] reissue_vpn_access неатомарна
**Файл:** `vpn_utils.py:811-883`
**Проблема:** Шаг 1 (удаление старого UUID) и шаг 2 (создание нового UUID) — два независимых вызова. Если шаг 1 прошёл, а шаг 2 упал — пользователь теряет VPN-доступ без возможности восстановления.
**Рекомендация:** Добавить compensating action: при ошибке шага 2 попытаться вернуть старый UUID.

### [E-BUG-3] upgrade_vless_user читает response после закрытия client
**Файл:** `vpn_utils.py:388-391`
**Проблема:** В `_make_request` обращение к `response.text` происходит после выхода из `async with httpx.AsyncClient()`. Если httpx не буферизовал body — чтение может не сработать.
**Рекомендация:** Перенести `response.text` / `response.json()` внутрь `async with` блока.

### [E-BUG-4] Trial scheduler singleton flag не сбрасывается
**Файл:** `trial_notifications.py:585-592`
**Проблема:** `_TRIAL_SCHEDULER_STARTED = True` устанавливается при запуске, но не сбрасывается при CancelledError. Если scheduler отменяется — его нельзя перезапустить.
**Рекомендация:** Добавить `finally: _TRIAL_SCHEDULER_STARTED = False` или использовать `asyncio.Event`.

### [E-BUG-5] reminders_task без worker lock
**Файл:** `reminders.py:157-213`
**Проблема:** В отличие от других воркеров, `reminders_task` не использует `_worker_lock`. Два экземпляра могут отправить дублированные напоминания.
**Рекомендация:** Добавить `_worker_lock` как в `activation_worker.py`.

## D5.3 Обновлённая статистика

Добавлено: 2 CRIT, 2 MED (безопасность), 5 BUG = +9 issues
**Итого по проекту: 29 critical/high, 76 medium, 4 low — 109 issues total.**

---

# ДОПОЛНЕНИЕ 6. MIDDLEWARE, УТИЛИТЫ, CORE

## D6.1 КРИТИЧЕСКИЕ БАГИ

### [U-CRIT-1] NameError при импорте i18n/types.py — _ar_plural
**Файл:** `app/core/i18n/types.py:35`
**Проблема:** В dict `PLURAL_RULES` (строка 29-38) ссылка `"ar": _ar_plural` на строке 35, но функция `_ar_plural` определена на строке 45 — **после** dict. При импорте модуля — `NameError: name '_ar_plural' is not defined`. Это означает, что арабский язык не работает вообще если этот модуль когда-либо импортируется напрямую.
**Рекомендация:** Переместить `_ar_plural` выше `PLURAL_RULES` или использовать отложенную инициализацию.

### [U-CRIT-2] AttributeError в pool_monitor — _conn не в __slots__
**Файл:** `app/core/pool_monitor.py:31,36`
**Проблема:** `__slots__ = ("pool", "label")` не включает `_conn`. Строка 36: `self._conn = None` и строка 40: `self._conn = await self.pool.acquire()` вызовут `AttributeError` когда `POOL_MONITOR_ENABLED=true`.
**Рекомендация:** Добавить `"_conn"` в `__slots__`: `__slots__ = ("pool", "label", "_conn")`.

## D6.2 СРЕДНИЕ ПРОБЛЕМЫ

### [U-MED-1] payment_webhook.py не лимитирует chunked requests
**Файл:** `app/api/__init__.py` (RequestSizeLimitMiddleware)
**Проблема:** Middleware проверяет только `Content-Length` header. При chunked transfer encoding (без Content-Length) тело запроса не лимитировано и может исчерпать память. `telegram_webhook.py` имеет свою проверку (1MB), но `payment_webhook.py` — нет.
**Рекомендация:** Добавить проверку размера body в payment_webhook или обрабатывать chunked encoding в middleware.

### [U-MED-2] Redis rate limiter считает запросы даже при active rate-limit
**Файл:** `app/core/rate_limit_middleware.py:96`
**Проблема:** `pipe.zadd(rate_key, {str(now): now})` добавляет entry при каждом вызове, даже когда пользователь уже rate-limited. Это ускоряет накопление flood ban быстрее чем задумано.
**Рекомендация:** Записывать request в sorted set только если пользователь НЕ rate-limited.

### [U-MED-3] Дублированные константы LOYALTY_IMAGES / LOYALTY_PHOTOS
**Файл:** `app/constants/loyalty.py`
**Проблема:** `LOYALTY_IMAGES` и `LOYALTY_PHOTOS` содержат идентичные данные. При обновлении одного — второй может не обновиться.
**Рекомендация:** Удалить один из словарей, оставить alias: `LOYALTY_PHOTOS = LOYALTY_IMAGES`.

### [U-MED-4] pool_monitor.py — pool.release() может быть async
**Файл:** `app/core/pool_monitor.py:63`
**Проблема:** `self.pool.release(self._conn)` вызывается синхронно. В asyncpg `pool.release()` является coroutine. Без `await` вызов может не выполниться.
**Рекомендация:** Заменить на `await self.pool.release(self._conn)`.

## D6.3 Обновлённая статистика

Добавлено: 2 CRIT, 4 MED = +6 issues
**Итого по проекту: 31 critical/high, 80 medium, 4 low — 115 issues total.**

---

# ДОПОЛНЕНИЕ 7. i18n — ГЛУБОКИЙ АНАЛИЗ ПЕРЕВОДОВ

## D7.1 КРИТИЧЕСКИЕ ПРОБЛЕМЫ ПЕРЕВОДОВ

### [I18N-CRIT-1] Казахский и таджикский — 36% ключей не переведены
**Файлы:** `app/i18n/kk.py`, `app/i18n/tj.py`
**Проблема:** По 258 ключей из 710 (36%) остаются на английском языке. Затронуты: весь admin namespace, многие main.* (welcome, settings, service status), payment.*, profile.*, buy.*. Эти языки фактически непригодны для пользователей.
**Рекомендация:** Применить имеющиеся патчи (`translation_patch_kk.json`, `translation_patch_tj.json`) и дозаказать перевод оставшихся ключей.

### [I18N-CRIT-2] Арабский — 94 ключа не переведены
**Файл:** `app/i18n/ar.py`
**Проблема:** 94 ключа на английском. Затронуты: admin panel, payment notifications, несколько user-facing ключей.

### [I18N-CRIT-3] 4 ключа с несовпадающими placeholder-ами между языками
**Ключи:**
- `referral.cashback_amount` — ru/en: `{amount:.2f}`, остальные: `{action_type}`, `{amount:.2f}` (лишний `{action_type}`)
- `referral.cashback_title` — ru/en: без placeholder, остальные: `{action_type}`
- `referral.registered_notification` — ru/en: `{date}`, `{first_payment_msg}`, остальные: + `{user}`
- `referral.trial_activated_notification` — ru/en: без placeholder, остальные: `{first_payment_msg}`, `{user}`
**Проблема:** При вызове `get_text` с набором kwargs из ru/en — у других языков KeyError (хотя get_text перехватывает format errors и возвращает raw string). Пользователь увидит нелокализованную строку с `{placeholder}`.
**Связано с:** [L-CRIT-12] (games.dice_success mismatch)

## D7.2 СРЕДНИЕ ПРОБЛЕМЫ

### [I18N-MED-1] 26 непримерённых translation patches
**Файлы:** `translation_patch_ar.json` (4), `translation_patch_de.json` (3), `translation_patch_kk.json` (7), `translation_patch_tj.json` (9), `translation_patch_uz.json` (3)
**Проблема:** Переводы подготовлены в JSON patch files, но не применены к .py файлам. Включая полностью отсутствующий ключ `main.trial_notification_54h` (18ч до конца триала).
**Рекомендация:** Написать скрипт для автоматического применения патчей и добавить в CI.

### [I18N-MED-2] Дублирование namespace: main.* и новые namespace
**Проблема:** Многие ключи существуют в двух namespace одновременно:
- `main.trial_notification_6h` ↔ `trial.notification_6h`
- `main.reminder_paid_3d` ↔ `reminder.paid_3d`
- `main.auto_renewal_success` ↔ `subscription.auto_renew_success`
При обновлении одного — второй может отстать, создавая рассинхрон.
**Рекомендация:** Завершить миграцию, удалить старые `main.*` дубли, обновить все вызовы `get_text`.

### [I18N-MED-3] Валидатор ложно помечает казахский и таджикский
**Файл:** `validate_language_content.py`
**Проблема:** `CYRILLIC_ALLOWED_KEYS` содержит только 3 ключа, но весь казахский и таджикский пишутся кириллицей. Валидатор ложно помечает их как нарушения.
**Рекомендация:** Добавить `kk` и `tj` в список языков, где кириллица допустима целиком.

### [I18N-MED-4] admin.reissue_user_notification — HTML теги только в non-ru
**Ключ:** `admin.reissue_user_notification`
**Проблема:** en/tj/de/kk/ar содержат `<b>` и `<code>` теги, а ru — plain text. Русская версия рендерится без форматирования.

## D7.3 Обновлённая статистика

Добавлено: 3 CRIT (i18n), 4 MED = +7 issues
**Итого по проекту: 34 critical/high, 84 medium, 4 low — 122 issues total.**

---

# ДОПОЛНЕНИЕ 8. МИГРАЦИИ — ДОПОЛНИТЕЛЬНЫЕ НАХОДКИ

### [M-HIGH-1] Migration 013 ссылается на несуществующий столбец
**Файл:** `migrations/013_fix_referrals_columns.sql`
**Проблема:** `ALTER TABLE referrals ALTER COLUMN first_paid_at DROP NOT NULL` — но столбец `first_paid_at` не создаётся ни в одной предыдущей миграции. Migration 003 создаёт referrals без этого столбца. Столбец создаётся динамически в `database/core.py:752`. Миграция упадёт если столбец не был создан приложением до запуска миграций.
**Рекомендация:** Добавить `ADD COLUMN IF NOT EXISTS first_paid_at TIMESTAMPTZ` перед `DROP NOT NULL`.

### [M-HIGH-2] Migration 022 может создать дубликаты UUID
**Файл:** `migrations/022_remove_uuid_prefix.sql`
**Проблема:** `UPDATE subscriptions SET uuid = regexp_replace(uuid, '^(stage-|prod-|test-)', '')` — если два UUID отличались только префиксом (`stage-abc-123` и `prod-abc-123`), после UPDATE оба станут `abc-123`. Migration 024 потом добавляет UNIQUE constraint на uuid — и упадёт.
**Рекомендация:** Добавить проверку на дубликаты перед UPDATE или обработать конфликты.

### [M-MED-5] Migration 024 — timezone conversion предполагает UTC
**Файл:** `migrations/024_schema_hardening_timestamptz_uuid_constraints.sql:12-26`
**Проблема:** `ALTER COLUMN expires_at TYPE TIMESTAMPTZ USING expires_at AT TIME ZONE 'UTC'` — предполагает что все данные хранились в UTC. Если какие-то записи были в local timezone, конвертация будет неверной (сдвиг на часовой пояс).
**Рекомендация:** Задокументировать pre-migration проверку: `SELECT DISTINCT date_part('timezone', expires_at) FROM subscriptions`.

**Обновлённая статистика: +2 HIGH, +1 MED = 36 critical/high, 85 medium, 4 low — 125 issues total.**

---

# ДОПОЛНЕНИЕ 9. I18N — ДОПОЛНИТЕЛЬНЫЕ НАХОДКИ (WORKER NOTIFICATIONS)

### [I18N-HIGH-1] Хардкод русского текста в уведомлениях воркеров
**Файлы:**
- `auto_renewal.py:339-341` — уведомление о продлении подписки
- `auto_renewal.py:239,268,289` — описания транзакций баланса
- `activation_worker.py:212-215` — welcome-сообщение для новых подписчиков
- `app/workers/farm_notifications.py:71-101` — 3 уведомления фермы (созрели/предупреждение/сгнили)
- `app/workers/farm_notifications.py:25-30` — русские имена растений в PLANT_TYPES fallback

**Проблема:** Эти воркеры отправляют уведомления пользователям с хардкод-текстом на русском. Пользователи с en/de/uz/tj/kk/ar языком получат русские сообщения. farm_notifications — наиболее критичный случай: 7+ строк хардкода.
**Рекомендация:** Загружать `lang` пользователя из БД и вызывать `get_text(lang, key)` для всех user-facing строк. Создать соответствующие i18n-ключи во всех языковых файлах.

### [I18N-HIGH-2] Placeholder mismatch в games.dice_success
**Файлы:** `app/i18n/ru.py` vs `app/i18n/en.py`
**Проблема:** EN-версия использует `{value}`, RU-версия использует `{date}` и `{value}`. Если код вызывает `get_text("ru", "games.dice_success", value=x)` без `date`, форматирование выбросит `KeyError`.
**Рекомендация:** Унифицировать плейсхолдеры во всех языковых файлах.

### [I18N-MED-5] 5 ключей отсутствуют в ru.py
**Файл:** `app/i18n/ru.py`
**Ключи:**
- `payment.success_welcome_basic`
- `payment.success_welcome_plus`
- `referral.cashback_referred`
- `referral.registered_user`
- `referral.trial_activated_user`

**Проблема:** Эти ключи есть в EN, но отсутствуют в RU. Fallback на английский — не критично, но нарушает UX для русскоязычных пользователей.
**Рекомендация:** Добавить переводы в ru.py.

### [W-MED-1] Отсутствие jitter при старте reminders и farm_notifications
**Файлы:**
- `reminders.py:160` — фиксированный `sleep(60)`
- `app/workers/farm_notifications.py:114` — фиксированный `sleep(60)`
**Проблема:** Все остальные воркеры используют `random.uniform(5, 60)` jitter, эти два — нет. При одновременном рестарте контейнеров оба проснутся ровно через 60с, создав пиковую нагрузку на пул.
**Рекомендация:** Добавить `jitter_s = random.uniform(5, 60)` как в остальных воркерах.

**Обновлённая статистика: +2 HIGH, +2 MED = 38 critical/high, 87 medium, 4 low — 129 issues total.**

---

# ДОПОЛНЕНИЕ 10. ТЕСТЫ — РАСШИРЕННЫЙ АНАЛИЗ

### [T-HIGH-1] Неверная assertion в тесте webhook-подписей
**Файл:** `tests/test_webhook_signatures.py:173`
```python
assert result["status"] != "unauthorized"  # Должно быть == "unauthorized"
```
**Проблема:** Тест `test_valid_auth_headers_accepted` передаёт `"wrong-secret"`, но проверяет `!= "unauthorized"`. Тест пройдёт при ЛЮБОМ статусе кроме "unauthorized" (включая "error", "ok", и т.д.). Если код не проверяет секрет — тест всё равно пройдёт.
**Рекомендация:** Исправить на `assert result["status"] == "unauthorized"` или создать отдельный тест для валидных и невалидных секретов.

### [T-HIGH-2] Функция should_expire_trial не тестируется
**Файл:** `tests/services/test_trials.py`
**Проблема:** Функция `should_expire_trial` импортирована (строка 15), но ни один тест её не вызывает. Комментарий на строке 45-46 признаёт: "requires database connection... better tested as integration test" — но интеграционного теста тоже нет.
**Рекомендация:** Создать тест с мокированной БД или integration-тест.

### [T-HIGH-3] Миграция 021 — swap PK без блокировки таблицы
**Файл:** `migrations/021_promo_lifecycle_schema.sql:40-56`
**Проблема:** DROP CONSTRAINT (PK) → ADD CONSTRAINT (новый PK). Между этими операциями параллельные INSERT могут создать дубликаты. PostgreSQL не блокирует INSERT во время DDL на constraint (ACCESS EXCLUSIVE lock берётся на каждый ALTER отдельно).
**Рекомендация:** Обернуть в `LOCK TABLE promo_codes IN ACCESS EXCLUSIVE MODE` перед DROP или выполнять в одном ALTER.

### [T-MED-2] CI не запускает миграции
**Файл:** `.github/workflows/ci.yml:53-54`
**Проблема:** PostgreSQL запускается как сервис, но `migrations.py` никогда не вызывается. Все тесты работают на моках. SQL-ошибки в миграциях (несуществующие столбцы, дубли UUID) не обнаруживаются в CI.
**Рекомендация:** Добавить шаг `python migrations.py` перед запуском тестов. Добавить хотя бы один тест, проверяющий что миграции проходят на чистой БД.

### [T-MED-3] Отсутствуют тестовые зависимости
**Файл:** `requirements.txt`
**Проблема:** Нет `pytest-cov` (coverage), `freezegun` (time mocking), `pytest-timeout`. Невозможно измерить покрытие, тесты могут зависать бесконечно.
**Рекомендация:** Добавить `pytest-cov>=5.0`, `pytest-timeout>=2.3`, `freezegun>=1.4`.

### [T-MED-4] Несогласованность ключа и часов в trial schedule
**Файл:** `tests/services/test_trials.py:183`
```python
assert schedule[1]["key"] == "trial.notification_60h"  # 60h
assert schedule[1]["hours"] == 48  # 48 часов, не 60
```
**Проблема:** Ключ i18n говорит "60h" (2.5 дня), но `hours=48` (2 дня). Либо ключ неверный, либо часы.
**Рекомендация:** Проверить бизнес-требования и унифицировать.

**Обновлённая статистика: +3 HIGH, +3 MED = 41 critical/high, 90 medium, 4 low — 135 issues total.**
