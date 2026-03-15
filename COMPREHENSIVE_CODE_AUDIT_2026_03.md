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
| Безопасность | 3 | 5 | 2 |
| Корректность логики | 4 | 5 | 0 |
| Производительность | 0 | 3 | 1 |
| Workers | 0 | 3 | 0 |
| **Итого** | **7** | **16** | **3** |

## Приоритеты исправления

### P0 (Немедленно):
1. [S-CRIT-2] — Webhook 200 при ошибке может терять платежи
2. [L-CRIT-3] — Нелокализованные уведомления автопродления
3. [L-CRIT-4] — Нелокализованные уведомления активации

### P1 (В ближайшие спринты):
1. [S-CRIT-1] — Platega webhook аутентификация
2. [S-CRIT-3] — MINI_APP_URL в config
3. [S-MED-5] — Advisory lock enforcement
4. [L-CRIT-1] — Проверить единицы баланса в auto_renewal
5. [L-CRIT-2] — create_payment всегда 30 дней

### P2 (При рефакторинге):
- Все средние проблемы безопасности и корректности
- Производительность export и metrics queries
- Workers consistency
