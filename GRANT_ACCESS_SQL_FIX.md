# Fix: asyncpg "expects 8 arguments, 9 were passed" in grant_access

## 1. Что было сломано

**Ошибка:**
```
asyncpg.exceptions.InvalidParameterValueError: 
the server expects 8 arguments for this query, 9 were passed
```

**Место:** `database.py:3557-3599` - функция `grant_access()`, INSERT запрос для новой подписки с UUID

**Причина:** Дублирование аргумента `activation_status` - передавалось 9 аргументов вместо 8

---

## 2. Какой аргумент лишний

**Лишний аргумент:** `activation_status` передавался дважды (строки 3597 и 3598)

**Было:**
```python
await conn.execute(
    """INSERT INTO subscriptions (...)
       VALUES ($1, $2, $3, $4, 'active', $5, ..., $8, ...)
       ON CONFLICT (telegram_id) DO UPDATE SET
           activation_status = $8, ...""",
    telegram_id, new_uuid, vless_url, subscription_end, source, admin_grant_days, subscription_start,
    'pending' if pending_activation else 'active',  # $8 - первый раз
    'pending' if pending_activation else 'active'   # $9 - ДУБЛИКАТ!
)
```

**Проблема:** SQL использует 8 плейсхолдеров ($1-$8), но передавалось 9 аргументов.

---

## 3. Какой SQL был

**SQL запрос (строки 3558-3595):**
```sql
INSERT INTO subscriptions (
    telegram_id, uuid, vpn_key, expires_at, status, source,
    reminder_sent, reminder_3d_sent, reminder_24h_sent,
    reminder_3h_sent, reminder_6h_sent, admin_grant_days,
    activated_at, last_bytes,
    trial_notif_6h_sent, trial_notif_18h_sent, trial_notif_30h_sent,
    trial_notif_42h_sent, trial_notif_54h_sent, trial_notif_60h_sent,
    trial_notif_71h_sent,
    activation_status, activation_attempts, last_activation_error
)
VALUES ($1, $2, $3, $4, 'active', $5, FALSE, FALSE, FALSE, FALSE, FALSE, $6, $7, 0,
        FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE,
        $8, 0, NULL)
ON CONFLICT (telegram_id) 
DO UPDATE SET 
    uuid = COALESCE($2, subscriptions.uuid),
    vpn_key = COALESCE($3, subscriptions.vpn_key),
    expires_at = $4,
    status = 'active',
    source = $5,
    reminder_sent = FALSE,
    reminder_3d_sent = FALSE,
    reminder_24h_sent = FALSE,
    reminder_3h_sent = FALSE,
    reminder_6h_sent = FALSE,
    admin_grant_days = $6,
    activated_at = COALESCE($7, subscriptions.activated_at),
    last_bytes = 0,
    trial_notif_6h_sent = FALSE,
    trial_notif_18h_sent = FALSE,
    trial_notif_30h_sent = FALSE,
    trial_notif_42h_sent = FALSE,
    trial_notif_54h_sent = FALSE,
    trial_notif_60h_sent = FALSE,
    trial_notif_71h_sent = FALSE,
    activation_status = $8,
    activation_attempts = 0,
    last_activation_error = NULL
```

**Плейсхолдеры:** $1, $2, $3, $4, $5, $6, $7, $8 = **8 плейсхолдеров**

**Аргументы (БЫЛО - НЕПРАВИЛЬНО):**
```python
telegram_id,           # $1
new_uuid,              # $2
vless_url,             # $3
subscription_end,       # $4
source,                # $5
admin_grant_days,      # $6
subscription_start,    # $7
'pending' if pending_activation else 'active',  # $8
'pending' if pending_activation else 'active'   # $9 - ЛИШНИЙ!
```

**Итого:** 9 аргументов при 8 плейсхолдерах → **ОШИБКА**

---

## 4. Какой SQL стал

**SQL запрос:** Без изменений (корректный, использует 8 плейсхолдеров)

**Аргументы (СТАЛО - ПРАВИЛЬНО):**
```python
args = (
    telegram_id,           # $1
    new_uuid,              # $2
    vless_url,             # $3
    subscription_end,      # $4
    source,                # $5
    admin_grant_days,      # $6
    subscription_start,    # $7
    'pending' if pending_activation else 'active'  # $8
)
await conn.execute(sql, *args)
```

**Итого:** 8 аргументов при 8 плейсхолдерах → **OK**

**Изменения:**
1. Удален дубликат `activation_status` (строка 3598)
2. Добавлен debug-log для валидации количества аргументов
3. Использован tuple `args` для явного контроля количества аргументов

---

## 5. Нужна ли миграция

**НЕТ** - миграция не нужна.

**Причина:**
- Колонка `activation_status` уже существует в таблице `subscriptions`
- Проблема была только в количестве аргументов, а не в структуре БД
- SQL-запрос корректен, ошибка была только в передаче аргументов

**Проверка существования колонки:**
Колонка `activation_status` добавляется в миграции (строка 541 в `database.py`):
```python
await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS activation_status TEXT DEFAULT 'active'")
```

---

## 6. Валидация после фикса

### 6.1. Проверка аргументов

**Debug-log добавлен:**
```python
logger.debug(
    f"grant_access: SQL_ARGS_COUNT [user={telegram_id}, "
    f"placeholders=8, args_count={len(args)}, "
    f"activation_status={activation_status_value}]"
)
```

**Ожидаемый вывод:**
```
grant_access: SQL_ARGS_COUNT [user=123456789, placeholders=8, args_count=8, activation_status=active]
```

### 6.2. Тестовые сценарии

**1. Trial activation:**
- ✅ Должно работать без ошибок
- ✅ В логах: `grant_access: SQL_ARGS_COUNT [placeholders=8, args_count=8]`
- ✅ В БД: `uuid = stage-*` (в STAGE), `activation_status = 'active'`

**2. Grant access (admin):**
- ✅ Должно работать без ошибок
- ✅ В логах: НЕТ "expects 8 arguments"
- ✅ В БД: подписка создана/обновлена корректно

**3. Payment subscription:**
- ✅ Должно работать без ошибок
- ✅ XRAY add-user вызывается 1 раз
- ✅ UUID сохраняется в БД

### 6.3. Проверка логов

**Должно быть:**
- ✅ `grant_access: SQL_ARGS_COUNT [placeholders=8, args_count=8]`
- ✅ `grant_access: SAVING_TO_DB [user=..., uuid=...]`
- ✅ `grant_access: NEW_ISSUANCE_SUCCESS [action=new_issuance, ...]`

**НЕ должно быть:**
- ❌ "the server expects 8 arguments for this query, 9 were passed"
- ❌ "asyncpg.exceptions.InvalidParameterValueError"

---

## 7. Детали исправления

### 7.1. Файл: `database.py`

**Строки:** 3555-3600

**Изменения:**
1. **Удален дубликат аргумента** (строка 3598):
   ```python
   # БЫЛО:
   'pending' if pending_activation else 'active',
   'pending' if pending_activation else 'active'  # ДУБЛИКАТ
   
   # СТАЛО:
   activation_status_value = 'pending' if pending_activation else 'active'
   args = (telegram_id, new_uuid, vless_url, subscription_end, source, admin_grant_days, subscription_start, activation_status_value)
   ```

2. **Добавлен debug-log** для валидации:
   ```python
   logger.debug(
       f"grant_access: SQL_ARGS_COUNT [user={telegram_id}, "
       f"placeholders=8, args_count={len(args)}, "
       f"activation_status={activation_status_value}]"
   )
   ```

3. **Использован tuple `args`** для явного контроля:
   ```python
   await conn.execute(sql, *args)
   ```

### 7.2. Почему это произошло

**Причина:** При добавлении поддержки `activation_status` в INSERT запрос, аргумент был добавлен дважды:
1. Первый раз - для VALUES ($8)
2. Второй раз - по ошибке (дубликат)

**Когда:** После включения XRAY в STAGE, когда начали активно тестировать grant_access

---

## 8. Rollback не нужен

**Причина:**
- Изменение только исправляет ошибку передачи аргументов
- Не меняет структуру БД
- Не меняет бизнес-логику
- Обратно совместимо

**Если нужно откатить:**
- Просто вернуть дубликат аргумента (но это вернет ошибку)

---

## 9. Связанные места

**Проверены другие INSERT/UPDATE в `grant_access`:**
- ✅ UPDATE для renewal (строка 3138) - корректен (2 аргумента)
- ✅ INSERT для pending activation (строка 3276) - корректен (5 аргументов)
- ✅ INSERT для new issuance (строка 3558) - **ИСПРАВЛЕН** (8 аргументов)

**Все остальные запросы в функции корректны.**

---

## 10. Итог

✅ **Проблема:** Дублирование аргумента `activation_status` (9 аргументов вместо 8)

✅ **Решение:** Удален дубликат, добавлен debug-log, использован tuple для контроля

✅ **Миграция:** Не нужна (колонка уже существует)

✅ **Rollback:** Не нужен (исправление ошибки)

✅ **Валидация:** Debug-log добавлен, тесты должны пройти
