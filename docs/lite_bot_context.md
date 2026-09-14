# Telegram VPN-bot — лёгкий стартовый контекст

Это контекст-спецификация для **быстрого клона** на основе архитектуры
текущего проекта (Atlas Secure), но без магазина, мульти-тарифов,
разных серверов и игровых механик. Один продукт, один сервер,
один платёжный поток. Документ можно вставить в новый проект как
стартовый промпт для AI-агента или как ТЗ для разработчика.

---

## 1. Что делает бот

Telegram-бот продаёт **одну** VPN-подписку. Сценарии пользователя:

1. `/start` → онбординг → выдача **триала на 3 дня** (одноразово).
2. По окончании триала / по желанию — оплата подписки (фикс-цена за
   месяц).
3. Бот хранит подписку, продлевает её при следующей оплате, выдаёт
   ссылку на VPN-клиент, шлёт напоминания за 24ч/3ч до конца.
4. Админ может: посмотреть статистику, выдать/отозвать доступ,
   сделать рассылку.

Что **не делаем** в лёгкой версии (вычеркнуто из исходного проекта):

- ❌ Несколько тарифов (Basic/Plus/Combo/Biz) → один тариф.
- ❌ Выбор страны / выделенные серверы → один общий сервер.
- ❌ Прокси, гифт-ссылки, реферальная система, ферма/штормы, бонусы.
- ❌ Бизнес-сегмент, мульти-валюта.
- ❌ Bypass-only (обход блокировок отдельно).
- ❌ Sub-tariff combo (флаг `is_combo`).

---

## 2. Стек

| Слой | Технология | Зачем |
|---|---|---|
| Бот | **aiogram 3.x** | Async-first Telegram framework |
| Хранилище | **PostgreSQL 14+** через `asyncpg` | Транзакции, TIMESTAMPTZ |
| Фоновые задачи | `asyncio.create_task` + scheduler-loop | Напоминания, очистка истёкших |
| VPN-провизия | **Remnawave panel** (REST) | Один сервер, без cluster-логики |
| Платежи | На выбор: Telegram Stars / YooKassa / Tribute | Один провайдер достаточно |
| Логирование | `logging` (stdlib) → stdout / journald | Простой setup |
| Деплой | Docker + Railway/VPS | Стандарт |

Версии Python: **3.11+** (требует `asyncio.TaskGroup` и совр. typing).

---

## 3. Структура папок

```
project/
├── main.py                  # запуск: pool + bot + scheduler
├── config.py                # все env-vars и константы (одно место)
├── database/
│   ├── __init__.py          # re-export всех функций
│   ├── core.py              # pool, init_db, _to_db_utc helpers
│   ├── users.py             # CRUD пользователей
│   └── subscriptions.py     # подписки, платежи, триал
├── app/
│   ├── handlers/
│   │   ├── common/          # /start, /help, экран профиля
│   │   ├── purchase/        # покупка / оплата
│   │   ├── trial/           # активация триала
│   │   └── admin/           # дашборд, рассылка, статистика
│   ├── services/
│   │   ├── vpn.py           # обёртка над Remnawave (add/update/delete)
│   │   ├── payments.py      # обёртка над платёжным шлюзом
│   │   └── notifications.py # фоновые напоминания
│   └── utils/
│       └── telegram_safe.py # safe_send, safe_edit (антипадение)
└── docs/
    └── ARCHITECTURE.md      # этот файл
```

Всё в одном репо. Нет микросервисов, нет очередей сообщений —
одиночный процесс с фоновыми тасками. Это **полностью покрывает**
до ~50k пользователей.

---

## 4. Схема БД (минимум)

```sql
-- Пользователи. PK = telegram_id, никакого внутреннего id.
CREATE TABLE users (
    telegram_id     BIGINT PRIMARY KEY,
    username        TEXT,
    language        TEXT DEFAULT 'ru',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    trial_used_at   TIMESTAMP,           -- когда активировали триал
    trial_expires_at TIMESTAMP,          -- когда триал кончается
    is_reachable    BOOLEAN DEFAULT TRUE -- false = заблокировал бота
);

-- Подписки. Один пользователь — одна активная строка.
CREATE TABLE subscriptions (
    telegram_id     BIGINT PRIMARY KEY REFERENCES users(telegram_id),
    vpn_uuid        TEXT,                -- UUID в Remnawave
    vpn_url         TEXT,                -- VLESS-ссылка
    expires_at      TIMESTAMPTZ NOT NULL,
    status          TEXT NOT NULL,       -- 'active' | 'expired' | 'pending'
    source          TEXT NOT NULL,       -- 'trial' | 'payment' | 'admin_grant'
    reminder_24h_sent BOOLEAN DEFAULT FALSE,
    reminder_3h_sent  BOOLEAN DEFAULT FALSE,
    activated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Платежи. Журнал.
CREATE TABLE payments (
    id              BIGSERIAL PRIMARY KEY,
    telegram_id     BIGINT NOT NULL REFERENCES users(telegram_id),
    invoice_id      TEXT UNIQUE,         -- external (Stars / YK / etc.)
    amount_kopecks  BIGINT NOT NULL,
    status          TEXT NOT NULL,       -- 'pending' | 'paid' | 'failed' | 'refunded'
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    paid_at         TIMESTAMPTZ
);

CREATE INDEX idx_subs_expiry ON subscriptions(expires_at) WHERE status='active';
CREATE INDEX idx_payments_status ON payments(status);
```

Всего три таблицы. Все остальные «фичи» из крупного бота
(переоформления, аудит-лог, рассылки) — это journalling и не входят
в MVP.

**Важно по типам:** в коде придерживайся `TIMESTAMPTZ` для всех
дат. `TIMESTAMP` без tz приводит к ошибке `operator does not exist:
timestamp without time zone > timestamp with time zone` при сравнении
с `NOW()`. Лучше **никогда** не смешивать наивные и aware datetime.

---

## 5. Ключевые потоки

### 5.1. `/start` → онбординг
- Существует пользователь? Нет → `INSERT INTO users`.
- Показать главное меню (4 кнопки): «📲 Подключить VPN», «💳 Купить»,
  «🆓 Триал на 3 дня», «❓ Помощь».
- Если триал уже использован → кнопка триала скрыта.

### 5.2. Триал
- Проверка `trial_used_at IS NULL` атомарно в одной транзакции.
- `UPDATE users SET trial_used_at=NOW(), trial_expires_at=NOW()+'3 days'`.
- Вызов `vpn.add_user(telegram_id, expires_at)` → получили `uuid` + `url`.
- `INSERT INTO subscriptions (source='trial', ...)`.
- Отправить ссылку пользователю.

Если шаг VPN провалился — `ROLLBACK`, `trial_used_at` не пишется,
пользователь может попробовать заново.

### 5.3. Покупка
1. Кнопка «Купить» → создать `payments` row со `status='pending'`.
2. Сгенерировать инвойс через платёжный провайдер → вернуть URL/`pay_url`.
3. Пользователь оплачивает.
4. Webhook платёжного провайдера → endpoint `/webhook/payment` →
   `UPDATE payments SET status='paid'` **в одной транзакции** с
   provision-операцией VPN.
5. Если VPN-провизия упала: **НЕ помечать payment как 'paid'** —
   webhook вернётся (провайдер ретраит), и при следующей попытке
   провизия будет идемпотентной.

**Главный принцип:** «пользователь оплатил → пользователь получил
то, что оплатил». Атомарность достигается через одну DB-транзакцию,
обёрнутую вокруг и записи платежа, и provision-вызова. Если VPN-API
не отвечает — `await` падает, транзакция откатывается, payment
остаётся `pending`, webhook повторится.

### 5.4. Продление
- Та же логика, что покупка, только `subscriptions.expires_at =
  GREATEST(expires_at, NOW()) + interval`. Это защищает от
  «двойного списания» если пользователь оплатил пока подписка ещё
  активна.

### 5.5. Напоминания
- Каждые 10 минут scheduler-loop вычитывает:
  `SELECT * FROM subscriptions WHERE status='active' AND expires_at
   BETWEEN NOW() AND NOW() + interval '24 hours' AND NOT reminder_24h_sent`.
- Отправляет уведомление, ставит флаг.
- Аналогично за 3 часа.
- Истёкшие (`expires_at < NOW()` AND `status='active'`) — пометить
  `status='expired'`, вызвать `vpn.delete_user(uuid)`.

---

## 6. Внешние интеграции

### 6.1. Remnawave (VPN)
Один HTTP-клиент, четыре метода:

```python
# app/services/vpn.py
async def add_user(telegram_id: int, expires_at: datetime) -> dict:
    """POST /api/users — вернуть {uuid, vless_url}."""
    ...

async def update_user_expiry(uuid: str, expires_at: datetime) -> None:
    """PATCH /api/users/{uuid} — продлить."""
    ...

async def delete_user(uuid: str) -> None:
    """DELETE /api/users/{uuid} — отозвать доступ."""
    ...

async def find_user(telegram_id: int) -> dict | None:
    """GET /api/users?username=tg_{id} — для recovery / sanity."""
    ...
```

Идентификатор в панели: `tg_{telegram_id}`. Этот формат позволяет
**восстанавливать** связь, если БД упала: пройти панель по username
и сверить.

### 6.2. Платежи
Минимум один провайдер. Самое простое — **Telegram Stars**
(встроено в Bot API, без webhook):

```python
# Создание инвойса
await bot.send_invoice(
    chat_id=user_id,
    title="VPN на 30 дней",
    description="Атлас Lite — безлимитный трафик",
    payload=f"sub:{user_id}",       # вернётся в pre_checkout_query
    currency="XTR",                  # Telegram Stars
    prices=[LabeledPrice(label="VPN", amount=99)],  # 99 stars
)

@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)

@router.message(F.successful_payment)
async def on_paid(message: Message):
    # message.successful_payment.total_amount, .invoice_payload
    await activate_subscription(message.from_user.id, days=30)
```

Webhook не нужен — всё в одном потоке.

### 6.3. Конфиг
Всё в env:

```python
# config.py
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")
REMNAWAVE_URL = os.getenv("REMNAWAVE_URL")
REMNAWAVE_TOKEN = os.getenv("REMNAWAVE_TOKEN")
PRICE_STARS = int(os.getenv("PRICE_STARS", "99"))
SUBSCRIPTION_DAYS = int(os.getenv("SUBSCRIPTION_DAYS", "30"))
TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "3"))
```

Никаких словарей с тарифами/странами/мультипликаторами.

---

## 7. Фоновые задачи (scheduler)

В `main.py`:

```python
async def main():
    pool = await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(...)

    # Фоны
    asyncio.create_task(reminder_loop(bot))
    asyncio.create_task(expiry_cleanup_loop(bot))

    await dp.start_polling(bot)
```

Каждый loop — простой `while True: await asyncio.sleep(N); ...` с
`try/except` вокруг тела, чтобы один сбой не убил цикл. Никакого
APScheduler/Celery не нужно.

---

## 8. Админ-дашборд

Один `/admin` хендлер (доступ по `from_user.id == ADMIN_TELEGRAM_ID`),
inline-кнопки:

- 📊 Статистика: всего юзеров, активных подписок, MRR.
- 👤 Найти пользователя (по id или @username) → действия: выдать N дней / отозвать / посмотреть историю.
- 📢 Рассылка: текст + опционально фото, по сегментам (все / активные / без подписки).

Никаких сложных воронок, A/B-тестов, сегментов «trial-day-2». Можно
добавить позже.

---

## 9. UX-паттерны (готовый кодовый словарь)

### Безопасный send / edit
Telegram возвращает `TelegramForbiddenError` если пользователь
заблокировал бота, и `TelegramBadRequest` если сообщение нельзя
редактировать. Оборачивайте всё в утилиты:

```python
async def safe_send(bot, user_id, text, **kw):
    try:
        return await bot.send_message(user_id, text, parse_mode="HTML", **kw)
    except TelegramForbiddenError:
        await db.mark_unreachable(user_id)
    except TelegramBadRequest as e:
        if "chat not found" in str(e).lower():
            await db.mark_unreachable(user_id)
        else:
            logger.exception("send_failed")
    return None
```

В рассылках — `Semaphore(15)` (Telegram-лимит ~30 msg/s, 15
бережёт от RetryAfter), батчи по 200 с паузой 2 секунды между
батчами, `return_exceptions=True` в `asyncio.gather`.

### FSM
Используем для редких многошаговых сценариев (например, ввод
данных при админ-выдаче доступа). Не злоупотреблять: большинство
экранов — это просто callback-data без состояния.

---

## 10. Грабли, на которые мы наступили (важно учесть в клоне)

1. **TIMESTAMP vs TIMESTAMPTZ**. Сравнение `naive_col > NOW()` падает.
   Везде используй **TIMESTAMPTZ** и `datetime.now(timezone.utc)`.
2. **Атомарная выдача доступа.** Payment-row и provision-VPN
   **обязательно** в одной транзакции. Иначе пользователь оплатил,
   но ничего не получил — самая болезненная категория жалоб.
3. **`init_db` на больших таблицах.** `ALTER TABLE IF NOT EXISTS`
   ждёт `ACCESS EXCLUSIVE LOCK` — на 100k+ строках может зависнуть.
   Ставь `SET lock_timeout='5s'; SET statement_timeout='20s';` в
   начале init-блока.
4. **Кастомные tg-emoji.** `<tg-emoji emoji-id="...">` требует, чтобы
   ID был доступен боту, иначе Telegram отвечает `DOCUMENT_INVALID`
   и сообщение не уходит. Безопасно — обычные Unicode эмодзи. Если
   нужны premium — бери ID из готовых публичных паков и проверяй на
   тестовом аккаунте.
5. **Custom emoji можно отдавать боту через формат Telegram-Ads**:
   `![🎁](tg://emoji?id=12345)` — добавь util `convert_tg_emoji()`,
   который превращает в `<tg-emoji>`. Удобно для рассылок.
6. **Идемпотентность recovery.** Если делаешь «починку» массовой
   операции — всегда GET-probe перед UPDATE, чтобы повторный запуск
   не сломал то, что уже починилось.
7. **Webhook → бот: НИКОГДА не клади тяжёлую логику в webhook
   handler.** Webhook отвечает 200 OK моментально, а реальная работа
   уходит в `asyncio.create_task`. Иначе провайдер ретраит, а ты
   получаешь дубли.
8. **Не амендь продакшен-коммиты.** Любой откат — отдельный новый
   коммит, чтобы git-история была честным журналом.

---

## 11. Стартовый чек-лист

- [ ] `BOT_TOKEN`, `DATABASE_URL`, `REMNAWAVE_URL`, `REMNAWAVE_TOKEN`,
      `ADMIN_TELEGRAM_ID` — в env.
- [ ] `init_db()` создаёт три таблицы + индексы при запуске.
- [ ] `/start` → онбординг с кнопкой триала.
- [ ] Триал — атомарная транзакция, 3 дня, одноразово.
- [ ] Покупка через Telegram Stars → активация подписки.
- [ ] Scheduler-loop: напоминания 24ч/3ч + expiry-cleanup.
- [ ] Админ-меню: статистика, выдать/отозвать, рассылка.
- [ ] `safe_send` / `safe_edit` обёртки для всех точек отправки.
- [ ] Логи в stdout, нормальный уровень INFO.
- [ ] Dockerfile + railway.toml (или Procfile).

Готово к деплою. По времени — **2–3 дня** на одного разработчика
с этого контекста до рабочего MVP.

---

## 12. Что добавлять во вторую итерацию (когда взлетит)

- Промокоды (одна таблица, одна функция применения).
- Реферальная программа (кешбэк на баланс).
- A/B-тест рассылок.
- Дашборд аналитики (cohort retention, LTV).
- Несколько тарифов и страны — но только когда первый тариф уже
  зарабатывает.

Не делай это сразу. **Один продукт, один путь оплаты, один сервер
до первой тысячи платящих** — это правило.
