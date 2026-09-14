# ATCbot — Баланс и Реферальная система: Полная документация

## 1. БАЛАНС

### 1.1 Хранение

| Параметр | Значение |
|---|---|
| **Поле** | `users.balance` |
| **Тип** | `INTEGER NOT NULL DEFAULT 0` |
| **Единица** | **Копейки** (100 = 1₽) |
| **Конвертация** | `float(balance) / 100.0` для отображения в рублях |

### 1.2 Операции с балансом

**Начисление** — `database.increase_balance(telegram_id, amount_rubles, source, description)`
- Конвертирует рубли → копейки: `round(amount * 100)`
- Атомарно: `pg_advisory_xact_lock(telegram_id)` + `SELECT FOR UPDATE`
- SQL: `UPDATE users SET balance = balance + $1 WHERE telegram_id = $2`
- Записывает в `balance_transactions` (type='topup'|'cashback'|'admin_adjustment')
- Sources: `'telegram_payment'`, `'admin'`, `'referral'`

**Списание** — `database.decrease_balance(telegram_id, amount_rubles, source, description)`
- Проверяет достаточность: `SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE`
- Возвращает `False` если `current_balance < amount_kopecks`
- SQL: `UPDATE users SET balance = balance - $1 WHERE telegram_id = $2`
- Sources: `'subscription_payment'`, `'admin'`, `'refund'`

**Получение** — `database.get_user_balance(telegram_id)` → `float` (рубли)

### 1.3 Пополнение баланса

| Параметр | Значение |
|---|---|
| Минимум | 100₽ |
| Максимум | 100,000₽ |
| Предустановленные суммы | 250₽, 750₽, 999₽ |
| Telegram Stars | 230⭐, 690⭐, 920⭐ (формула: `amount * 1.7 / 1.85`) |

**Способы пополнения:**
- Банковская карта (YooKassa / Telegram Payments)
- СБП (Platega)
- Telegram Stars
- Lava (карта)
- Крипто (CryptoBot)

**Поток:**
1. Пользователь вводит сумму → выбирает способ оплаты
2. Создаётся invoice с payload `balance_topup_{telegram_id}_{amount}_{timestamp}`
3. После оплаты → `finalize_balance_topup()`:
   - Создаёт запись в `payments`
   - Увеличивает баланс (идемпотентно по `provider_charge_id`)
   - Начисляет реферальный кешбэк (если есть referrer)

### 1.4 Оплата подписки с баланса

**Handler:** `callback_pay_balance()` → `database.finalize_balance_purchase()`

**Поток (двухфазный):**
1. **Фаза 1** (вне транзакции): Создание VPN-аккаунта через API
2. **Фаза 2** (внутри транзакции):
   - `pg_advisory_xact_lock(telegram_id)`
   - `SELECT balance FROM users FOR UPDATE`
   - Проверка `balance >= amount_kopecks`
   - `UPDATE users SET balance = balance - amount_kopecks`
   - Запись в `balance_transactions` (type='subscription_payment')
   - Активация подписки через `grant_access()`
   - Создание записи в `payments`
   - Начисление реферального вознаграждения

**Возвращает:**
```json
{
  "success": true,
  "payment_id": 123,
  "expires_at": "2026-05-10T...",
  "vpn_key": "vless://...",
  "is_renewal": false,
  "new_balance": 451.0,
  "referral_reward": {"success": true, "percent": 10, "reward_amount": 29.9}
}
```

### 1.5 Вывод средств

| Параметр | Значение |
|---|---|
| Минимум | 500₽ |
| Максимум | 1,000,000₽ |

**Поток:**
1. Пользователь вводит сумму + реквизиты
2. `create_withdrawal_request()` — списывает баланс сразу, создаёт заявку
3. Админ approve → статус 'approved'
4. Админ reject → статус 'rejected', баланс возвращается (refund)

---

## 2. РЕФЕРАЛЬНАЯ СИСТЕМА

### 2.1 Реферальный код

| Параметр | Значение |
|---|---|
| **Поле** | `users.referral_code` |
| **Формат** | 6-символьный хеш (опакный) |
| **Ссылка** | `t.me/bot?start=ref_{code}` |
| **Legacy** | `ref_{telegram_id}` (числовой, поддерживается) |

### 2.2 Регистрация реферала

**Функция:** `process_referral_registration(telegram_id, referral_code)`

**Правила:**
- `referrer_id` **иммутабельный** — устанавливается один раз
- Самореферал заблокирован
- Циклические рефералы заблокированы
- Только для новых пользователей (при первом `/start`)

**SQL:**
```sql
UPDATE users SET referrer_id = $1, referred_by = $1
WHERE telegram_id = $2 AND referrer_id IS NULL AND referred_by IS NULL

INSERT INTO referrals (referrer_user_id, referred_user_id, is_rewarded, reward_amount)
VALUES ($1, $2, FALSE, 0)
```

### 2.3 Активация реферала

**Функция:** `activate_referral(telegram_id, activation_type)`

**Триггеры активации:**
- Первая оплата подписки
- Первое пополнение баланса
- Активация триала

**SQL:** `UPDATE referrals SET first_paid_at = NOW() WHERE ... AND first_paid_at IS NULL`

### 2.4 Уровни лояльности и кешбэк

| Оплативших рефералов | Кешбэк % | Уровень |
|---|---|---|
| 0–24 | **10%** | Стартовый |
| 25–49 | **25%** | Продвинутый |
| 50+ | **45%** | Партнёр |

**Считается по:** `COUNT(DISTINCT buyer_id) FROM referral_rewards WHERE referrer_id = $1`
(не приглашённых, а **оплативших**)

### 2.5 Начисление кешбэка

**Функция:** `process_referral_reward(buyer_id, purchase_id, amount_rubles, conn)`

**Вызывается когда:**
- Пользователь с `referrer_id` оплачивает подписку (любым способом)
- Из `finalize_balance_purchase()` и `finalize_purchase()`

**Расчёт:**
```python
reward_kopecks = round(amount_rubles * percent / 100 * 100)
```

**Защиты:**
1. Самореферал: `if referrer_id == buyer_id → return False`
2. Идемпотентность: `SELECT FROM referral_rewards WHERE buyer_id = $1 AND purchase_id = $2`
3. Кешбэк множитель: может быть 2x во время промо-акций

**SQL операции:**
```sql
-- Увеличение баланса реферера
UPDATE users SET balance = balance + reward_kopecks WHERE telegram_id = referrer_id

-- Запись транзакции
INSERT INTO balance_transactions (user_id, amount, type, source, description, related_user_id)
VALUES (referrer_id, reward_kopecks, 'cashback', 'referral', '...', buyer_id)

-- Запись вознаграждения
INSERT INTO referral_rewards (referrer_id, buyer_id, purchase_id, purchase_amount, percent, reward_amount)
VALUES (...)
```

**Возвращает:**
```json
{
  "success": true,
  "referrer_id": 123456,
  "percent": 10,
  "reward_amount": 29.9,
  "paid_referrals_count": 5,
  "referrals_needed": 20,
  "message": "..."
}
```

### 2.6 Уведомление реферера

После начисления кешбэка отправляется уведомление через `send_referral_cashback_notification()` с информацией о сумме покупки, кешбэке, текущем уровне и сколько рефералов до следующего уровня.

---

## 3. СХЕМА БАЗЫ ДАННЫХ

### users (релевантные поля)
```sql
balance INTEGER NOT NULL DEFAULT 0     -- Копейки
referral_code TEXT                     -- Уникальный 6-символьный код
referrer_id BIGINT                     -- Кто пригласил
referred_by BIGINT                     -- Legacy, = referrer_id
```

### balance_transactions
```sql
id SERIAL PRIMARY KEY
user_id BIGINT NOT NULL
amount NUMERIC NOT NULL                -- Копейки (может быть отрицательным)
type TEXT NOT NULL                     -- 'topup'|'cashback'|'subscription_payment'|'admin_adjustment'|'withdrawal'|'refund'
source TEXT                            -- 'telegram_payment'|'admin'|'referral'|'subscription_payment'|...
description TEXT
related_user_id BIGINT                 -- Связанный пользователь (buyer/referrer)
created_at TIMESTAMP DEFAULT NOW()
```

### referrals
```sql
id SERIAL PRIMARY KEY
referrer_user_id BIGINT NOT NULL
referred_user_id BIGINT NOT NULL UNIQUE
created_at TIMESTAMP DEFAULT NOW()
is_rewarded BOOLEAN DEFAULT FALSE
reward_amount INTEGER DEFAULT 0
first_paid_at TIMESTAMP               -- Когда реферал первый раз оплатил
```

### referral_rewards
```sql
id SERIAL PRIMARY KEY
referrer_id BIGINT NOT NULL
buyer_id BIGINT NOT NULL
purchase_id TEXT                       -- Для идемпотентности
purchase_amount INTEGER NOT NULL       -- Копейки
percent INTEGER NOT NULL               -- 10, 25 или 45
reward_amount INTEGER NOT NULL         -- Копейки
created_at TIMESTAMP DEFAULT NOW()

UNIQUE INDEX (buyer_id, purchase_id)   -- Предотвращает дубликаты
```

---

## 4. ОТВЕТЫ НА ВОПРОСЫ

### По балансу:

**1. Поле баланса:** `users.balance`, INTEGER, хранится в **копейках**.

**2. Начисление:** Через `increase_balance()`. Пополнение доступно через все платёжные методы (карта, СБП, Stars, Lava, крипто). API для внешнего пополнения — нет отдельного эндпоинта, только через payment webhooks.

**3. Оплата с баланса:** Да, полностью реализовано. `finalize_balance_purchase()` — атомарное списание + активация подписки в одной транзакции.

**4. API для синхронизации:** На данный момент **нет внешнего API** для синхронизации баланса. Бот — единственный источник правды. Для интеграции с сайтом нужно создать эндпоинт.

### По реферальной системе:

**5. Кешбэк:** Да, **полностью реализован**. Автоматически начисляется при каждой оплате реферала. Прогрессивная шкала 10%→25%→45%.

**6. Обработка оплаты реферала:** Бот обрабатывает **свой процесс** (`process_referral_reward()`). Не вызывает внешних API. Сумма покупки передаётся, кешбэк рассчитывается внутри.

**7. Уровень лояльности:** Каждая сторона может считать сама по `paid_referrals_count`. Для синхронизации достаточно передать количество оплативших рефералов.

### По синхронизации:

**8. Авторитетный источник:** Сейчас **бот — единственный источник** для баланса. Для двусторонней интеграции нужно определить кто primary.

**9. Формат вызова:** Не реализован. Рекомендуемый формат:
```json
POST /api/sync/balance
{
  "telegramId": 123456,
  "balance": 45100,           // копейки
  "lastTransactionId": 789
}
```

---

## 5. МЕХАНИЗМЫ БЕЗОПАСНОСТИ

1. **Advisory Locks:** `pg_advisory_xact_lock(telegram_id)` для всех финансовых операций
2. **SELECT FOR UPDATE:** Блокировка строки при чтении баланса
3. **Атомарные транзакции:** `async with conn.transaction()` для multi-step операций
4. **Идемпотентность:** `purchase_id` + `provider_charge_id` для предотвращения дубликатов
5. **Хранение в копейках:** INTEGER вместо FLOAT для точности
6. **DB Readiness:** Все операции проверяют `DB_READY` перед выполнением
