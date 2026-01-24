# database.py Contract Fixes - Summary

## ✅ Fixes Applied

### 1. `grant_access` Function (Line 2971)

**Issue:** Docstring claimed function could return None, but code always raises exceptions.

**Fix Applied:**
- ✅ Updated docstring to clarify: "Guaranteed to return a dict. Never returns None."
- ✅ Removed misleading "Или None при ошибке" text
- ✅ Added explicit Raises section

**Type Hint:** Already correct (`-> Dict[str, Any]`)

---

### 2. `admin_grant_access_atomic` Function (Line 5920)

**Issue:** 
- Type hint allowed `Optional[datetime], Optional[str]` but function never returns None
- Docstring claimed it could return `(None, None)` but code always raises

**Fix Applied:**
- ✅ Updated type hint: `Tuple[Optional[datetime], Optional[str]]` → `Tuple[datetime, str]`
- ✅ Updated docstring: Removed "(None, None) при ошибке", added explicit Raises section
- ✅ Clarified return value structure

**Backward Compatibility:** ✅ Safe
- Handlers already treat as side-effect (don't check return values)
- Function behavior unchanged (still raises on error)
- Only type hints and docs changed

---

### 3. `admin_grant_access_minutes_atomic` Function (Line 6244)

**Issue:** Same as admin_grant_access_atomic

**Fix Applied:**
- ✅ Updated type hint: `Tuple[Optional[datetime], Optional[str]]` → `Tuple[datetime, str]`
- ✅ Updated docstring: Removed "(None, None) при ошибке", added explicit Raises section
- ✅ Clarified return value structure

**Backward Compatibility:** ✅ Safe
- Handlers already treat as side-effect (don't check return values)
- Function behavior unchanged (still raises on error)
- Only type hints and docs changed

---

## 📊 Verification Results

### Code Path Analysis

**`grant_access`:**
- ✅ All 3 success paths return dict
- ✅ Exception path raises (never returns None)
- ✅ No implicit None returns

**`admin_grant_access_atomic`:**
- ✅ Success path returns `(datetime, str)` - both values guaranteed:
  - `expires_at` from `result["subscription_end"]` (always present)
  - `final_vpn_key` is either vless_url, vpn_key from DB, or uuid (fallback to ""), never None
- ✅ Exception path raises (never returns None)

**`admin_grant_access_minutes_atomic`:**
- ✅ Success path returns `(datetime, str)` - same logic
- ✅ Exception path raises (never returns None)

---

## 🔍 Handler Compatibility Check

**Handlers using these functions:**
1. `callback_admin_grant_minutes` (line 7988) - treats as side-effect ✅
2. `callback_admin_grant_quick_notify_fsm` (line 8406) - treats as side-effect ✅
3. `callback_admin_grant_quick_notify_fsm` (line 8446) - treats as side-effect ✅

**All handlers:**
- ✅ Don't check return values for None
- ✅ Treat functions as side-effects
- ✅ Handle exceptions correctly
- ✅ Type hint changes are safe (more strict, but handlers don't rely on Optional)

---

## 📝 Files Modified

1. **database.py:**
   - Line 3029-3036: Updated `grant_access` docstring
   - Line 5920: Updated `admin_grant_access_atomic` type hint and docstring
   - Line 6244: Updated `admin_grant_access_minutes_atomic` type hint and docstring

2. **Documentation:**
   - `DATABASE_CONTRACT_ANALYSIS.md` - Detailed analysis
   - `DATABASE_CONTRACT_FIXES.md` - This summary

---

## ✅ Definition of Done

- ✅ All functions have explicit return contracts
- ✅ All code paths return values or raise exceptions
- ✅ No implicit None returns
- ✅ Docstrings match actual behavior
- ✅ Type hints match actual behavior
- ✅ Backward compatibility preserved
- ✅ No breaking changes
- ✅ Handlers remain compatible

---

## 🎯 Impact

**Before:**
- Type hints allowed None but functions never returned None
- Docstrings claimed None returns but code raised exceptions
- Contract confusion for developers

**After:**
- Type hints accurately reflect behavior (no Optional where not needed)
- Docstrings accurately describe behavior (raises on error)
- Clear contracts for all functions
- Better type safety and IDE support

---

## ⚠️ Note on Empty String Returns

Both `admin_grant_access_*` functions can return empty string `""` for `vpn_key` in fallback cases:
- If `vless_url` is None (renewal case)
- AND `subscription_row["vpn_key"]` is None
- THEN `final_vpn_key = result.get("uuid", "")` (fallback to empty string)

This is acceptable behavior - empty string is a valid return value indicating no key available. If stricter validation is needed, it should be added in handlers, not in database layer.

---

## ✅ Testing Recommendations

1. **Unit Tests:**
   - Verify functions raise exceptions on error (not return None)
   - Verify functions return proper types on success

2. **Integration Tests:**
   - Test admin grant flows with handlers
   - Verify handlers handle exceptions correctly

3. **Type Checking:**
   - Run mypy/pyright to verify type hints are correct
   - Verify no type errors in handlers

---

## Summary

All function contracts are now explicit and accurate:
- ✅ `grant_access`: Always returns Dict, never None
- ✅ `admin_grant_access_atomic`: Always returns Tuple[datetime, str], never None
- ✅ `admin_grant_access_minutes_atomic`: Always returns Tuple[datetime, str], never None

All functions raise exceptions on error instead of returning None, which is the correct behavior for atomic operations.
