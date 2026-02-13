# ATLAS SECURE — WITHDRAWAL + BALANCE MANAGEMENT
# PRE-IMPLEMENTATION AUDIT REPORT

**Date:** 2026-02-11  
**Status:** SYSTEM ALREADY IMPLEMENTED — POST-IMPLEMENTATION AUDIT  
**Auditor:** Cursor AI

---

## EXECUTIVE SUMMARY

Система управления балансом и вывода средств **УЖЕ РЕАЛИЗОВАНА**. Проведен аудит реализованного кода.

**ВЕРДИКТ:** ⚠️ **NOT READY FOR PRODUCTION** — требуется исправление критических проблем перед деплоем.

---

## PART 1 — BALANCE ARCHITECTURE AUDIT

### 1.1 Balance Storage

✅ **НАЙДЕНО:**
- **Тип:** `INTEGER` (копейки)
- **Таблица:** `users.balance`
- **Миграция:** `002_add_balance.sql` (строка 16)
- **Constraint:** `balance_non_negative CHECK (balance >= 0)` добавлен в `018_withdrawal_requests_and_balance_constraint.sql` (строка 32)

✅ **КОРРЕКТНО:** Баланс хранится в копейках как INTEGER, constraint добавлен.

### 1.2 Functions Modifying Balance

**НАЙДЕНО 8 функций/мест изменения баланса:**

1. ✅ `increase_balance()` — `database.py:1039-1088`
   - Использует транзакцию: ✅
   - Использует SELECT FOR UPDATE: ❌
   - Использует advisory lock: ❌
   - **РИСК:** MEDIUM — возможна гонка при параллельных пополнениях

2. ✅ `decrease_balance()` — `database.py:1090-1160`
   - Использует транзакцию: ✅
   - Использует SELECT FOR UPDATE: ❌
   - Использует advisory lock: ❌
   - Проверяет баланс перед списанием: ✅ (строка 1122-1132)
   - **РИСК:** CRITICAL — возможна гонка при параллельных списаниях

3. ✅ `create_withdrawal_request()` — `database.py:1316-1361`
   - Использует транзакцию: ✅
   - Использует advisory lock: ✅ (строка 1332)
   - Проверяет баланс: ✅ (строка 1333-1339)
   - **РИСК:** LOW — защищено advisory lock

4. ✅ `reject_withdrawal_request()` — `database.py:1395-1428`
   - Использует транзакцию: ✅
   - Использует SELECT FOR UPDATE: ✅ (строка 1405)
   - Использует advisory lock: ✅ (строка 1412)
   - **РИСК:** LOW — защищено

5. ⚠️ `add_balance()` — `database.py:1202-1240` (legacy)
   - Использует транзакцию: ✅
   - Использует advisory lock: ❌
   - **РИСК:** MEDIUM — legacy функция, но используется в некоторых местах

6. ⚠️ `subtract_balance()` — `database.py:1243-1294` (legacy)
   - Использует транзакцию: ✅
   - Использует advisory lock: ❌
   - **РИСК:** MEDIUM — legacy функция

7. ✅ `finalize_balance_purchase()` — `database.py:6790-6807`
   - Использует транзакцию: ✅ (внутри `finalize_purchase`)
   - Использует SELECT FOR UPDATE: ❌
   - Использует advisory lock: ❌
   - **РИСК:** CRITICAL — гонка при параллельной покупке подписки и выводе

8. ✅ `process_referral_reward()` — `database.py:2406-2410`
   - Использует транзакцию: ✅
   - Использует advisory lock: ❌
   - **РИСК:** LOW — только увеличение баланса

### 1.3 CRITICAL FINDINGS

**🔴 CRITICAL RISK #1: Race Condition в `decrease_balance()`**

**Файл:** `database.py:1118-1160`  
**Проблема:** Между проверкой баланса (строка 1122) и UPDATE (строка 1135) возможна гонка.

**Сценарий:**
```
T1: SELECT balance → 1000 копеек
T2: SELECT balance → 1000 копеек (параллельно)
T1: UPDATE balance = balance - 800 → 200
T2: UPDATE balance = balance - 500 → 500 (ДОЛЖНО БЫТЬ ОТКЛОНЕНО!)
```

**Решение:** Добавить `SELECT ... FOR UPDATE` или advisory lock.

---

**🔴 CRITICAL RISK #2: Race Condition в `finalize_balance_purchase()`**

**Файл:** `database.py:6790-6807`  
**Проблема:** Прямой UPDATE без проверки баланса и без advisory lock.

**Сценарий:**
```
T1: Пользователь выводит 1000 ₽ → create_withdrawal_request (advisory lock)
T2: Одновременно покупает подписку за 500 ₽ → finalize_balance_purchase
T1: Списание 1000 → баланс = 0
T2: Списание 500 → баланс = -500 (VIOLATES CONSTRAINT!)
```

**Решение:** Добавить advisory lock в `finalize_balance_purchase()` перед списанием баланса.

---

**🟡 MEDIUM RISK #3: `increase_balance()` без advisory lock**

**Файл:** `database.py:1039-1088`  
**Проблема:** Параллельные пополнения могут привести к потере данных (маловероятно, но возможно).

**Решение:** Добавить advisory lock для консистентности.

---

## PART 2 — CONCURRENCY AUDIT

### SCENARIO A: Пользователь выводит + покупает подписку

**Текущая реализация:**
- Вывод: `create_withdrawal_request()` — ✅ advisory lock
- Покупка: `finalize_balance_purchase()` — ❌ НЕТ advisory lock

**Результат:** 🔴 **RACE CONDITION** — возможен отрицательный баланс.

**Исправление:** Добавить `pg_advisory_xact_lock(telegram_id)` в `finalize_balance_purchase()` перед строкой 6796.

---

### SCENARIO B: Два вывода одновременно

**Текущая реализация:**
- Оба используют `create_withdrawal_request()` с advisory lock

**Результат:** ✅ **SAFE** — advisory lock сериализует операции.

---

### SCENARIO C: Админ снимает + пользователь выводит

**Текущая реализация:**
- Админ: `decrease_balance()` — ❌ НЕТ advisory lock
- Пользователь: `create_withdrawal_request()` — ✅ advisory lock

**Результат:** 🔴 **RACE CONDITION** — возможен отрицательный баланс.

**Исправление:** Добавить advisory lock в `decrease_balance()`.

---

## PART 3 — FSM AUDIT

### 3.1 FSM States

✅ **НАЙДЕНО:**
- `WithdrawStates` — `handlers.py:865-869`
  - `withdraw_amount`
  - `withdraw_confirm`
  - `withdraw_requisites`
  - `withdraw_final_confirm`
- `AdminDebitBalance` — `handlers.py:857-859`
  - `waiting_for_amount`
  - `waiting_for_confirmation`

✅ **КОРРЕКТНО:** FSM состояния разделены, конфликтов нет.

### 3.2 FSM Security

**Проверка состояния:**
- ✅ `callback_withdraw_final_confirm` использует `StateFilter(WithdrawStates.withdraw_final_confirm)` — строка 2783
- ✅ `callback_withdraw_confirm_amount` использует `StateFilter(WithdrawStates.withdraw_confirm)` — строка 2747

**Отмена/Назад:**
- ✅ `callback_withdraw_cancel` очищает state — строка 2841
- ⚠️ **ПРОБЛЕМА:** Нет централизованного cancel handler для всех FSM состояний

**Риски зависших состояний:**
- 🟡 MEDIUM: Если пользователь отправит `/start` во время FSM, состояние может остаться
- 🟡 MEDIUM: Если бот перезапустится, FSM state в памяти потеряется (используется MemoryStorage)

**Рекомендация:** Добавить `@router.message(Command("start"))` который очищает FSM state.

---

## PART 4 — ADMIN APPROVAL FLOW AUDIT

### 4.1 Approval Protection

✅ **НАЙДЕНО:**
- `approve_withdrawal_request()` — `database.py:1376-1392`
  - Использует `WHERE status = 'pending'` — ✅ защита от повторной обработки
  - НЕТ `SELECT ... FOR UPDATE` — ⚠️ возможна гонка между двумя админами

- `reject_withdrawal_request()` — `database.py:1395-1428`
  - Использует `SELECT ... FOR UPDATE` — ✅ (строка 1405)
  - Использует `WHERE status = 'pending'` — ✅

### 4.2 CRITICAL FINDING

**🔴 CRITICAL RISK #4: Двойное подтверждение админом**

**Файл:** `database.py:1385-1388`  
**Проблема:** Два админа могут одновременно подтвердить одну заявку.

**Сценарий:**
```
Admin1: UPDATE withdrawal_requests SET status='approved' WHERE id=1 AND status='pending'
Admin2: UPDATE withdrawal_requests SET status='approved' WHERE id=1 AND status='pending' (параллельно)
Результат: Оба UPDATE успешны (если выполняются в разных транзакциях до commit)
```

**Решение:** Добавить `SELECT ... FOR UPDATE` в `approve_withdrawal_request()`:

```sql
SELECT id FROM withdrawal_requests WHERE id = $1 AND status = 'pending' FOR UPDATE
```

---

**🟡 MEDIUM RISK #5: Устаревший callback**

**Файл:** `handlers.py:2847-2873`  
**Проблема:** Если админ нажмет на старую кнопку (заявка уже обработана), получает только alert "Заявка уже обработана", но это не критично.

**Статус:** ✅ ACCEPTABLE — UX issue, не security issue.

---

## PART 5 — NOTIFICATION AUDIT

### 5.1 Admin Notification

✅ **НАЙДЕНО:**
- Уведомление админу отправляется в `callback_withdraw_final_confirm()` — строка 2831
- Используется `config.ADMIN_TELEGRAM_ID` — ✅
- Структурированное сообщение с wid, user_id, amount, requisites — ✅

⚠️ **ПРОБЛЕМА:** Нет correlation_id для трейсинга withdrawal flow.

**Рекомендация:** Добавить correlation_id = `f"withdraw_{wid}"` в логирование.

---

### 5.2 User Notification

✅ **НАЙДЕНО:**
- Уведомление пользователю при approve — `handlers.py:2862-2866`
- Уведомление пользователю при reject — `handlers.py:2890-2895`
- Обработка ошибок отправки — ✅ (try/except)

✅ **КОРРЕКТНО:** Уведомления защищены от ошибок.

---

## PART 6 — UI / NAVIGATION IMPACT

### 6.1 Main Menu Changes

✅ **НАЙДЕНО:**
- Замена "О сервисе" на "⚪️ Наша экосистема" — `handlers.py:958`
- Добавление "⚙️ Настройки" — `handlers.py:965`
- Удаление "Изменить язык" из главного меню — ✅

✅ **КОРРЕКТНО:** Изменения применены корректно.

### 6.2 Profile Changes

✅ **НАЙДЕНО:**
- Добавлена кнопка "💸 Вывести средства" — `handlers.py:1036-1039`

✅ **КОРРЕКТНО:** Кнопка добавлена в правильное место.

### 6.3 Callback Data Conflicts

✅ **ПРОВЕРЕНО:**
- `withdraw_start` — ✅ уникален
- `withdraw_approve:{wid}` — ✅ уникален
- `withdraw_reject:{wid}` — ✅ уникален
- `admin:debit_balance:{id}` — ✅ уникален

✅ **КОРРЕКТНО:** Конфликтов callback_data нет.

---

## PART 7 — SECURITY AUDIT

### 7.1 FSM Bypass

⚠️ **MEDIUM RISK #6: Обход FSM через crafted callback**

**Файл:** `handlers.py:2783`  
**Проблема:** `callback_withdraw_final_confirm` проверяет `StateFilter(WithdrawStates.withdraw_final_confirm)`, но если злоумышленник знает структуру FSM, может попытаться вызвать напрямую.

**Защита:** ✅ StateFilter защищает от прямого вызова без правильного FSM state.

**Статус:** ✅ ACCEPTABLE — защита есть.

---

### 7.2 Amount Validation

✅ **НАЙДЕНО:**
- Минимум 500 ₽ проверяется в `process_withdraw_amount()` — строка 2728
- Проверка `amount <= balance` — строка 2732

✅ **КОРРЕКТНО:** Валидация суммы есть.

---

### 7.3 Status Check

✅ **НАЙДЕНО:**
- Проверка `status = 'pending'` в `approve_withdrawal_request()` — строка 1386
- Проверка `status = 'pending'` в `reject_withdrawal_request()` — строка 1405
- Проверка в handlers перед вызовом DB функций — `handlers.py:2856, 2885`

✅ **КОРРЕКТНО:** Статус проверяется на всех уровнях.

---

## PART 8 — PERFORMANCE AUDIT

### 8.1 Blocking Operations

✅ **НАЙДЕНО:**
- Все DB операции используют `async/await` — ✅
- Все операции в транзакциях — ✅
- Нет long-running синхронных операций — ✅

✅ **КОРРЕКТНО:** Performance issues не обнаружены.

---

## SUMMARY — CRITICAL RISKS

### 🔴 CRITICAL (MUST FIX BEFORE PRODUCTION)

1. **Race Condition в `decrease_balance()`**
   - **Файл:** `database.py:1118-1160`
   - **Исправление:** Добавить `SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE` перед UPDATE

2. **Race Condition в `finalize_balance_purchase()`**
   - **Файл:** `database.py:6790-6807`
   - **Исправление:** Добавить `pg_advisory_xact_lock(telegram_id)` перед списанием баланса

3. **Race Condition: Админ снимает + пользователь выводит**
   - **Файл:** `database.py:1090-1160`
   - **Исправление:** Добавить advisory lock в `decrease_balance()`

4. **Двойное подтверждение админом**
   - **Файл:** `database.py:1376-1392`
   - **Исправление:** Добавить `SELECT ... FOR UPDATE` в `approve_withdrawal_request()`

### 🟡 MEDIUM (SHOULD FIX)

5. **`increase_balance()` без advisory lock**
   - **Файл:** `database.py:1039-1088`
   - **Исправление:** Добавить advisory lock для консистентности

6. **Нет централизованного FSM cancel handler**
   - **Файл:** `handlers.py`
   - **Исправление:** Добавить очистку FSM state в `/start` handler

7. **Нет correlation_id для withdrawal flow**
   - **Файл:** `handlers.py:2783-2833`
   - **Исправление:** Добавить correlation_id в логирование

### 🟢 LOW (NICE TO HAVE)

8. Legacy функции `add_balance()` и `subtract_balance()` без advisory lock
9. MemoryStorage для FSM — потеря состояния при перезапуске (ожидаемое поведение)

---

## RECOMMENDED ARCHITECTURE PATTERNS

### Pattern 1: Atomic Balance Update

```python
async def decrease_balance_safe(telegram_id: int, amount: float, ...):
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", telegram_id)
        current = await conn.fetchval(
            "SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE",
            telegram_id
        )
        if current < amount_kopecks:
            return False
        await conn.execute("UPDATE users SET balance = balance - $1 WHERE telegram_id = $2", ...)
```

### Pattern 2: Idempotent Approval

```python
async def approve_withdrawal_request_safe(wid: int, processed_by: int):
    async with conn.transaction():
        row = await conn.fetchrow(
            "SELECT id FROM withdrawal_requests WHERE id = $1 AND status = 'pending' FOR UPDATE",
            wid
        )
        if not row:
            return False
        await conn.execute("UPDATE withdrawal_requests SET status='approved' ...")
```

---

## FINAL VERDICT

⚠️ **NOT READY FOR PRODUCTION**

**Критические проблемы:**
- 4 CRITICAL race conditions могут привести к отрицательному балансу или двойной обработке
- Требуется исправление перед деплоем в production

**Рекомендации:**
1. Исправить все 4 CRITICAL проблемы
2. Исправить MEDIUM проблемы #5, #6, #7
3. Провести нагрузочное тестирование после исправлений
4. Добавить мониторинг отрицательных балансов (alert если constraint violation)

**После исправлений:** ✅ READY FOR PRODUCTION

---

**Audit completed:** 2026-02-11  
**Next steps:** Fix critical issues → Test → Deploy
