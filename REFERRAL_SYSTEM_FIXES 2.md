# Referral System End-to-End Restoration

## Summary

Полное восстановление реферальной системы с исправлением всех критических проблем:
- Регистрация рефералов при /start
- Активация trial с пометкой реферала как активного
- Начисление кешбэка при пополнении баланса
- Начисление кешбэка при покупке подписки
- Единый сервис уведомлений
- Корректная статистика

## Files Modified

### 1. `database.py`
- **Added**: `mark_referral_active()` - пометка реферала как активного при trial
- **Fixed**: `approve_payment_atomic()` - заменён старый код рефералов на вызов `process_referral_reward()`
- **Fixed**: `finalize_purchase()` - добавлен вызов `process_referral_reward()` для пополнения баланса
- **Enhanced**: Логирование с префиксами `REFERRAL_REGISTERED`, `REFERRAL_CASHBACK_GRANTED`, `REFERRAL_NOTIFICATION_SENT`

### 2. `handlers.py`
- **Fixed**: `/start` handler - улучшено логирование регистрации рефералов
- **Fixed**: `callback_activate_trial()` - добавлен вызов `mark_referral_active()` после активации trial
- **Fixed**: `send_referral_cashback_notification()` - использует единый сервис форматирования
- **Fixed**: `process_successful_payment()` - добавлен период подписки в уведомление
- **Fixed**: `callback_approve_payment()` - добавлена отправка уведомлений для admin-approved платежей

### 3. `app/services/notifications/service.py`
- **Added**: `format_referral_notification_text()` - единая функция форматирования уведомлений о реферальном кешбэке

## Business Logic Flow

### A) START Handler
1. Парсинг реферальной ссылки: `/start ref_<telegram_id>`
2. Проверки:
   - Self-referral запрещён
   - referrer_id устанавливается только один раз
   - Защита от циклов рефералов
3. Регистрация через `register_referral()` - сохраняет referrer_id в users и создаёт запись в referrals

### B) Database Layer
- `users.referrer_id` - основной источник истины
- `referrals` таблица - отслеживание активных рефералов
- `referral_rewards` - история начислений кешбэка (idempotency)

### C) Trial Activation
1. Активация trial через `grant_access(source="trial")`
2. Вызов `mark_referral_active()` - создаёт запись в referrals если её нет
3. **НЕ начисляет кешбэк** (trial бесплатный)

### D) Balance Top-up Flow
1. `finalize_purchase()` для `period_days == 0`
2. Пополнение баланса через `increase_balance()`
3. Вызов `process_referral_reward()` - начисляет кешбэк рефереру
4. Отправка уведомления через `send_referral_cashback_notification()`

### E) Purchase Flow
1. `finalize_purchase()` для `period_days > 0`
2. Активация подписки через `grant_access()`
3. Вызов `process_referral_reward()` - начисляет кешбэк рефереру
4. Отправка уведомления с периодом подписки

### F) Notifications
- Единый сервис: `app/services/notifications/service.py::format_referral_notification_text()`
- Включает: username, сумму, кешбэк, период подписки, прогресс до следующего уровня
- Отправка через `send_referral_cashback_notification()` в handlers

### G) Statistics
- `get_referral_level_info()` - использует `referrals.first_paid_at` для подсчёта оплативших
- Уровень определяется по **оплатившим** рефералам, не по приглашённым
- Прогрессивная шкала: 0-24 → 10%, 25-49 → 25%, 50+ → 45%

## Key Fixes

### 1. Referral Registration
**Problem**: referrer_id не сохранялся при /start
**Fix**: Улучшена логика в `/start` handler с правильными проверками и логированием

### 2. Trial Activation
**Problem**: Trial не помечал реферала как активного
**Fix**: Добавлен вызов `mark_referral_active()` после активации trial

### 3. Balance Top-up Cashback
**Problem**: Кешбэк не начислялся при пополнении баланса
**Fix**: Добавлен вызов `process_referral_reward()` в `finalize_purchase()` для balance top-up

### 4. Purchase Cashback
**Problem**: Старый код в `approve_payment_atomic()` использовал неправильную логику (одноразовый кешбэк)
**Fix**: Заменён на вызов `process_referral_reward()` с правильной логикой (кешбэк при каждой оплате)

### 5. Notifications
**Problem**: Разные форматы уведомлений, отсутствие периода подписки
**Fix**: Единый сервис форматирования с поддержкой периода подписки

### 6. Statistics
**Problem**: Статистика могла быть неточной
**Fix**: Используется `referrals.first_paid_at` для подсчёта оплативших рефералов

## Idempotency & Safety

1. **Referral Registration**: `register_referral()` использует `ON CONFLICT DO NOTHING`
2. **Cashback**: `process_referral_reward()` проверяет `referral_rewards` по `purchase_id` для защиты от дубликатов
3. **Notifications**: Отправка уведомлений не блокирует транзакции (try/except)
4. **Self-referral Protection**: Проверка `referrer_id == buyer_id` во всех потоках

## Logging

Все события логируются с префиксами:
- `REFERRAL_REGISTERED` - регистрация реферала
- `REFERRAL_MARKED_ACTIVE` - пометка реферала как активного
- `REFERRAL_CASHBACK_GRANTED` - начисление кешбэка
- `REFERRAL_NOTIFICATION_SENT` - отправка уведомления
- `REFERRAL_SELF_ATTEMPT` - попытка самореферала
- `REFERRAL_FRAUD` - обнаружение мошенничества

## Testing Checklist

### Manual Testing Steps

1. **Referral Registration**
   - [ ] Создать реферальную ссылку: `/start ref_<telegram_id>`
   - [ ] Новый пользователь переходит по ссылке
   - [ ] Проверить: `users.referrer_id` установлен
   - [ ] Проверить: запись в `referrals` создана
   - [ ] Проверить логи: `REFERRAL_REGISTERED`

2. **Trial Activation**
   - [ ] Реферал активирует trial
   - [ ] Проверить: запись в `referrals` существует
   - [ ] Проверить логи: `REFERRAL_MARKED_ACTIVE`
   - [ ] Проверить: кешбэк НЕ начислен (trial бесплатный)

3. **Balance Top-up**
   - [ ] Реферал пополняет баланс
   - [ ] Проверить: кешбэк начислен рефереру
   - [ ] Проверить: запись в `referral_rewards`
   - [ ] Проверить: уведомление отправлено рефереру
   - [ ] Проверить логи: `REFERRAL_CASHBACK_GRANTED`, `REFERRAL_NOTIFICATION_SENT`

4. **Purchase**
   - [ ] Реферал покупает подписку
   - [ ] Проверить: кешбэк начислен рефереру
   - [ ] Проверить: запись в `referral_rewards`
   - [ ] Проверить: уведомление с периодом подписки отправлено
   - [ ] Проверить логи: `REFERRAL_CASHBACK_GRANTED`, `REFERRAL_NOTIFICATION_SENT`

5. **Statistics**
   - [ ] Проверить `/menu_referral` - корректное отображение статистики
   - [ ] Проверить: `paid_referrals_count` соответствует реальности
   - [ ] Проверить: уровень кешбэка корректный

6. **Edge Cases**
   - [ ] Self-referral блокируется
   - [ ] Повторная регистрация реферала игнорируется
   - [ ] Дубликат кешбэка блокируется (idempotency)
   - [ ] Уведомления не блокируют транзакции при ошибках

## Database Schema

### `users` table
- `referrer_id BIGINT` - Telegram ID реферера (устанавливается один раз)
- `referred_by BIGINT` - для обратной совместимости

### `referrals` table
- `referrer_user_id BIGINT` - реферер
- `referred_user_id BIGINT` - реферал (UNIQUE)
- `is_rewarded BOOLEAN` - устаревшее (не используется)
- `reward_amount INTEGER` - устаревшее (не используется)
- `first_paid_at TIMESTAMP` - дата первой оплаты реферала (используется для статистики)

### `referral_rewards` table
- `referrer_id BIGINT` - реферер
- `buyer_id BIGINT` - покупатель (реферал)
- `purchase_id TEXT` - ID покупки (для idempotency, UNIQUE)
- `purchase_amount INTEGER` - сумма покупки в копейках
- `percent INTEGER` - процент кешбэка
- `reward_amount INTEGER` - сумма кешбэка в копейках

## Cashback Calculation

Прогрессивная шкала на основе **оплативших** рефералов:
- 0-24 оплативших → 10%
- 25-49 оплативших → 25%
- 50+ оплативших → 45%

Подсчёт оплативших: `COUNT(DISTINCT referred_user_id) FROM referrals WHERE referrer_user_id = $1 AND first_paid_at IS NOT NULL`

## Notes

- Кешбэк начисляется при **каждой** оплате реферала (не только при первой)
- Trial активация **не начисляет** кешбэк (бесплатно)
- Все операции идемпотентны и защищены от дубликатов
- Уведомления отправляются асинхронно и не блокируют транзакции
