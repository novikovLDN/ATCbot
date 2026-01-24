# database.py Function Contract Analysis

## Executive Summary

**Functions Analyzed:** 3 critical functions  
**Contract Mismatches Found:** 3  
**Implicit None Returns:** 0 (all functions either return values or raise)  
**Docstring Issues:** 3 (docstrings claim None returns but code raises exceptions)

---

## 1. Function Contract Analysis

### ✅ `grant_access` (Line 2971)

**Current Return Type:** `-> Dict[str, Any]`  
**Docstring Claims:** "Или None при ошибке"  
**Actual Behavior:** Always returns Dict or raises Exception (never returns None)

**Code Paths:**
1. Line 3217: Returns dict for renewal case ✅
2. Line 3358: Returns dict for pending_activation case ✅
3. Line 3645: Returns dict for new_issuance case ✅
4. Line 3658: Raises exception (doesn't return None) ✅

**Issue:** Docstring is incorrect - function never returns None, always raises on error.

**Fix Required:**
- Update docstring to say "Raises Exception on error" instead of "Или None при ошибке"
- Type hint is correct (Dict[str, Any], not Optional)

---

### ❌ `admin_grant_access_atomic` (Line 5920)

**Current Return Type:** `-> Tuple[Optional[datetime], Optional[str]]`  
**Docstring Claims:** "(expires_at, vpn_key) или (None, None) при ошибке"  
**Actual Behavior:** Always returns Tuple or raises Exception (never returns None)

**Code Paths:**
1. Line 5968: Returns `(expires_at, final_vpn_key)` ✅
2. Line 5970-5972: Raises exception (doesn't return None) ✅

**Issue:** 
- Docstring claims it can return (None, None) but code always raises
- Type hint allows None but function never returns None
- Contract mismatch causes confusion in handlers

**Fix Required:**
- Update docstring to say "Raises Exception on error"
- Update type hint to `-> Tuple[datetime, str]` (remove Optional)
- Ensure all code paths return values (already done - raises on error)

---

### ❌ `admin_grant_access_minutes_atomic` (Line 6244)

**Current Return Type:** `-> Tuple[Optional[datetime], Optional[str]]`  
**Docstring Claims:** "(expires_at, vpn_key) или (None, None) при ошибке"  
**Actual Behavior:** Always returns Tuple or raises Exception (never returns None)

**Code Paths:**
1. Line 6296: Returns `(expires_at, final_vpn_key)` ✅
2. Line 6298-6300: Raises exception (doesn't return None) ✅

**Issue:** Same as admin_grant_access_atomic - contract mismatch

**Fix Required:**
- Update docstring to say "Raises Exception on error"
- Update type hint to `-> Tuple[datetime, str]` (remove Optional)
- Ensure all code paths return values (already done - raises on error)

---

## 2. Code Path Verification

### `grant_access` Return Paths

✅ **Path 1: Renewal (Line 3100-3222)**
- Returns: `{"uuid": uuid, "vless_url": None, "subscription_end": subscription_end, "action": "renewal"}`
- Always returns dict

✅ **Path 2: Pending Activation (Line 3241-3363)**
- Returns: `{"uuid": None, "vless_url": None, "subscription_end": subscription_end, "action": "pending_activation"}`
- Always returns dict

✅ **Path 3: New Issuance (Line 3381-3650)**
- Returns: `{"uuid": new_uuid, "vless_url": vless_url, "subscription_end": subscription_end, "action": "new_issuance"}`
- Always returns dict

✅ **Path 4: Exception (Line 3652-3658)**
- Raises exception (doesn't return None)
- Properly handled

**Conclusion:** All code paths return values or raise. No implicit None returns.

---

### `admin_grant_access_atomic` Return Paths

✅ **Path 1: Success (Line 5936-5968)**
- Returns: `(expires_at, final_vpn_key)`
- Both values are guaranteed to exist:
  - `expires_at` comes from `result["subscription_end"]` (always present in grant_access return)
  - `final_vpn_key` is either `result["vless_url"]`, `subscription_row["vpn_key"]`, or `result.get("uuid", "")` (fallback to empty string, never None)

✅ **Path 2: Exception (Line 5970-5972)**
- Raises exception (doesn't return None)

**Issue:** `final_vpn_key` could be empty string `""` but never None. Type hint allows None but code never returns None.

**Conclusion:** All code paths return values or raise. No implicit None returns, but type hint is too permissive.

---

### `admin_grant_access_minutes_atomic` Return Paths

✅ **Path 1: Success (Line 6260-6296)**
- Returns: `(expires_at, final_vpn_key)`
- Same logic as admin_grant_access_atomic

✅ **Path 2: Exception (Line 6298-6300)**
- Raises exception (doesn't return None)

**Conclusion:** All code paths return values or raise. No implicit None returns, but type hint is too permissive.

---

## 3. Exact Code Fixes

### Fix 1: Update `grant_access` Docstring

**File:** `database.py`  
**Location:** Line 3029-3036

**Change:**
```python
# OLD:
Returns:
    {
        "uuid": str,
        "vless_url": Optional[str],  # только если новый UUID
        "subscription_end": datetime
    }
    
    Или None при ошибке

Raises:
    Exception: При любых ошибках (не возвращает None, выбрасывает исключение)

# NEW:
Returns:
    Dict[str, Any] with keys:
        - "uuid": Optional[str] - UUID (None for pending activation)
        - "vless_url": Optional[str] - VLESS URL (None for renewal, present for new issuance)
        - "subscription_end": datetime - Subscription expiration date
        - "action": str - "renewal", "new_issuance", or "pending_activation"
    
    Guaranteed to return a dict. Never returns None.

Raises:
    Exception: При любых ошибках (транзакция откатывается, исключение пробрасывается)
```

---

### Fix 2: Update `admin_grant_access_atomic` Contract

**File:** `database.py`  
**Location:** Line 5920-5932

**Change:**
```python
# OLD:
async def admin_grant_access_atomic(telegram_id: int, days: int, admin_telegram_id: int) -> Tuple[Optional[datetime], Optional[str]]:
    """Атомарно выдать доступ пользователю на N дней (админ)
    
    Использует единую функцию grant_access (защищена от двойного создания ключей).
    
    Args:
        telegram_id: Telegram ID пользователя
        days: Количество дней доступа (1, 7 или 14)
        admin_telegram_id: Telegram ID администратора
    
    Returns:
        (expires_at, vpn_key) или (None, None) при ошибке или отсутствии ключей
    """

# NEW:
async def admin_grant_access_atomic(telegram_id: int, days: int, admin_telegram_id: int) -> Tuple[datetime, str]:
    """Атомарно выдать доступ пользователю на N дней (админ)
    
    Использует единую функцию grant_access (защищена от двойного создания ключей).
    
    Args:
        telegram_id: Telegram ID пользователя
        days: Количество дней доступа (1, 7 или 14)
        admin_telegram_id: Telegram ID администратора
    
    Returns:
        Tuple[datetime, str]: (expires_at, vpn_key)
        - expires_at: Дата истечения подписки
        - vpn_key: VPN ключ (vless_url для нового UUID, vpn_key из подписки для продления, или uuid как fallback)
    
    Raises:
        Exception: При любых ошибках (транзакция откатывается, исключение пробрасывается)
        Гарантированно возвращает значения или выбрасывает исключение. Никогда не возвращает None.
    """
```

---

### Fix 3: Update `admin_grant_access_minutes_atomic` Contract

**File:** `database.py`  
**Location:** Line 6244-6256

**Change:**
```python
# OLD:
async def admin_grant_access_minutes_atomic(telegram_id: int, minutes: int, admin_telegram_id: int) -> Tuple[Optional[datetime], Optional[str]]:
    """Атомарно выдать доступ пользователю на N минут (админ)
    
    Использует единую функцию grant_access (защищена от двойного создания ключей).
    
    Args:
        telegram_id: Telegram ID пользователя
        minutes: Количество минут доступа (например, 10)
        admin_telegram_id: Telegram ID администратора
    
    Returns:
        (expires_at, vpn_key) или (None, None) при ошибке или отсутствии ключей
    """

# NEW:
async def admin_grant_access_minutes_atomic(telegram_id: int, minutes: int, admin_telegram_id: int) -> Tuple[datetime, str]:
    """Атомарно выдать доступ пользователю на N минут (админ)
    
    Использует единую функцию grant_access (защищена от двойного создания ключей).
    
    Args:
        telegram_id: Telegram ID пользователя
        minutes: Количество минут доступа (например, 10)
        admin_telegram_id: Telegram ID администратора
    
    Returns:
        Tuple[datetime, str]: (expires_at, vpn_key)
        - expires_at: Дата истечения подписки
        - vpn_key: VPN ключ (vless_url для нового UUID, vpn_key из подписки для продления, или uuid как fallback)
    
    Raises:
        Exception: При любых ошибках (транзакция откатывается, исключение пробрасывается)
        Гарантированно возвращает значения или выбрасывает исключение. Никогда не возвращает None.
    """
```

---

## 4. Verification: All Code Paths Return Values

### `grant_access`
- ✅ Renewal path: Returns dict
- ✅ Pending activation path: Returns dict
- ✅ New issuance path: Returns dict
- ✅ Exception path: Raises (doesn't return None)

### `admin_grant_access_atomic`
- ✅ Success path: Returns `(datetime, str)` - both values guaranteed:
  - `expires_at = result["subscription_end"]` (always present)
  - `final_vpn_key` is either vless_url, vpn_key from DB, or uuid (fallback to ""), never None
- ✅ Exception path: Raises (doesn't return None)

### `admin_grant_access_minutes_atomic`
- ✅ Success path: Returns `(datetime, str)` - same logic as admin_grant_access_atomic
- ✅ Exception path: Raises (doesn't return None)

**Note:** `final_vpn_key` can be empty string `""` but never None. If we want to be strict, we could check for empty string and raise, but current behavior is acceptable (empty string is a valid return value indicating no key available).

---

## 5. Summary of Changes

### Files Modified:
- `database.py`

### Changes:
1. ✅ Update `grant_access` docstring (remove "Или None при ошибке")
2. ✅ Update `admin_grant_access_atomic` type hint and docstring
3. ✅ Update `admin_grant_access_minutes_atomic` type hint and docstring

### Backward Compatibility:
- ✅ No breaking changes (function behavior unchanged)
- ✅ Type hints become more strict (Optional removed) but this is safe because:
  - Handlers already treat these as side-effects (don't check return values)
  - Functions never returned None anyway
  - Only docstrings and type hints change

### Testing:
- ✅ All existing tests should pass (behavior unchanged)
- ✅ Type checkers will be more accurate
- ✅ Handlers already handle exceptions correctly
