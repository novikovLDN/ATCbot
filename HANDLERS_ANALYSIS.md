# handlers.py Analysis & Fixes

## Executive Summary

**Total Callback Patterns:** 91  
**Total Registered Handlers:** 60  
**Missing Handlers:** 2 critical (admin:notify:yes, admin:notify:no without :minutes:)  
**None Return Issues:** 2 handlers (grant_days, grant_1_year)  
**Refactoring Priority:** Medium (admin handlers scattered, ~3000 lines)

---

## 1. Callback Data → Handler Mapping

### ✅ Properly Handled Callbacks

| Callback Pattern | Handler | Status |
|-----------------|---------|--------|
| `admin:notify:yes:minutes:*` | `callback_admin_grant_minutes_notify` | ✅ Fixed |
| `admin:notify:no:minutes:*` | `callback_admin_grant_minutes_notify` | ✅ Fixed |
| `admin:revoke:user:*` | `callback_admin_revoke` | ✅ Fixed |
| `admin:revoke:notify:yes` | `callback_admin_revoke_notify` | ✅ Fixed |
| `admin:revoke:notify:no` | `callback_admin_revoke_notify` | ✅ Fixed |

### ❌ Missing Handlers

| Callback Pattern | Used In | Handler | Status |
|-----------------|---------|---------|--------|
| `admin:notify:yes` | `callback_admin_grant_days` (line 7952) | **MISSING** | ❌ Critical |
| `admin:notify:no` | `callback_admin_grant_days` (line 7953) | **MISSING** | ❌ Critical |
| `admin:notify:yes` | `callback_admin_grant_1_year` (line 8046) | **MISSING** | ❌ Critical |
| `admin:notify:no` | `callback_admin_grant_1_year` (line 8047) | **MISSING** | ❌ Critical |

**Impact:** When admin selects "1 day", "7 days", "14 days", or "1 year" grant, the notify buttons are unhandled because:
- `callback_admin_grant_quick_notify` requires FSM state `AdminGrantAccess.waiting_for_notify`
- But `callback_admin_grant_days` and `callback_admin_grant_1_year` don't execute grant first
- They save to FSM and show buttons, but grant happens in the notify handler
- If FSM is cleared or state is wrong, buttons become unhandled

---

## 2. None Return Value Issues

### Issue 1: `callback_admin_grant_days`

**Location:** Line 7928-7964  
**Problem:** 
- Doesn't execute grant before showing notify buttons
- Relies on `callback_admin_grant_quick_notify` to execute grant
- That handler checks `if not expires_at or not vpn_key: raise Exception`
- But `admin_grant_access_atomic` may return None

**Current Flow:**
```
admin:grant_days:{user_id}:{days}
  → callback_admin_grant_days
    → Save to FSM
    → Show notify buttons (admin:notify:yes/no)
      → callback_admin_grant_quick_notify (if FSM state matches)
        → Execute grant
        → Check return value (may fail if None)
```

**Fix Required:** Execute grant FIRST, then show notify buttons (like minutes handler)

### Issue 2: `callback_admin_grant_1_year`

**Location:** Line 8023-8058  
**Problem:** Same as grant_days - doesn't execute grant first

**Fix Required:** Execute grant FIRST, then show notify buttons

---

## 3. Exact Fixes

### Fix 1: Add Missing Handler for `admin:notify:yes` and `admin:notify:no`

**File:** `handlers.py`  
**Location:** After line 8362 (after `callback_admin_grant_minutes_notify`)

```python
@router.callback_query(
    (F.data == "admin:notify:yes") | (F.data == "admin:notify:no"),
    StateFilter(AdminGrantAccess.waiting_for_notify)
)
async def callback_admin_grant_quick_notify_fsm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Handle notify choice for grant_days and grant_1_year (FSM-based flow).
    This handler works WITH FSM state (unlike minutes handler which is FSM-free).
    """
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав доступа", show_alert=True)
        return
    
    await callback.answer()
    
    try:
        notify = callback.data == "admin:notify:yes"
        data = await state.get_data()
        user_id = data.get("user_id")
        action_type = data.get("action_type")
        
        if not user_id or not action_type:
            logger.warning(f"Missing FSM data: user_id={user_id}, action_type={action_type}")
            await callback.answer("Ошибка: данные не найдены", show_alert=True)
            await state.clear()
            return
        
        logger.info(f"ADMIN_GRANT_NOTIFY_SELECTED [notify={notify}, user_id={user_id}, action_type={action_type}]")
        
        # Execute grant based on action_type
        if action_type == "grant_days":
            days = data.get("days")
            if not days:
                logger.error(f"Missing days in FSM for grant_days")
                await callback.answer("Ошибка: данные не найдены", show_alert=True)
                await state.clear()
                return
            
            # Execute grant (treat as side-effect, don't check return value)
            try:
                await database.admin_grant_access_atomic(
                    telegram_id=user_id,
                    days=days,
                    admin_telegram_id=callback.from_user.id
                )
                # If no exception → grant is successful
            except Exception as e:
                logger.exception(f"Failed to grant access: {e}")
                await callback.answer("Ошибка выдачи доступа", show_alert=True)
                await state.clear()
                return
            
            expires_str = "N/A"  # We don't check return value
            text = f"✅ Доступ выдан на {days} дней"
            
            if notify:
                try:
                    user_text = f"Администратор выдал вам доступ на {days} дней"
                    await bot.send_message(user_id, user_text)
                    logger.info(f"NOTIFICATION_SENT [type=admin_grant, user_id={user_id}, days={days}]")
                    text += "\nПользователь уведомлён."
                except Exception as e:
                    logger.exception(f"Error sending notification: {e}")
                    text += "\nОшибка отправки уведомления."
            else:
                logger.info(f"ADMIN_GRANT_NOTIFY_SKIPPED [user_id={user_id}, days={days}]")
                text += "\nДействие выполнено без уведомления."
            
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard())
            
            # Audit log
            await database._log_audit_event_atomic_standalone(
                "admin_grant_access",
                callback.from_user.id,
                user_id,
                f"Admin granted {days} days access, notify_user={notify}"
            )
        
        elif action_type == "grant_1_year":
            # Execute grant (treat as side-effect, don't check return value)
            try:
                await database.admin_grant_access_atomic(
                    telegram_id=user_id,
                    days=365,
                    admin_telegram_id=callback.from_user.id
                )
                # If no exception → grant is successful
            except Exception as e:
                logger.exception(f"Failed to grant access: {e}")
                await callback.answer("Ошибка выдачи доступа", show_alert=True)
                await state.clear()
                return
            
            text = "✅ Доступ на 1 год выдан"
            
            if notify:
                try:
                    user_text = "Администратор выдал вам доступ на 1 год"
                    await bot.send_message(user_id, user_text)
                    logger.info(f"NOTIFICATION_SENT [type=admin_grant, user_id={user_id}, duration=1_year]")
                    text += "\nПользователь уведомлён."
                except Exception as e:
                    logger.exception(f"Error sending notification: {e}")
                    text += "\nОшибка отправки уведомления."
            else:
                logger.info(f"ADMIN_GRANT_NOTIFY_SKIPPED [user_id={user_id}, duration=1_year]")
                text += "\nДействие выполнено без уведомления."
            
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard())
            
            # Audit log
            await database._log_audit_event_atomic_standalone(
                "admin_grant_access_1_year",
                callback.from_user.id,
                user_id,
                f"Admin granted 1 year access, notify_user={notify}"
            )
        
        else:
            logger.warning(f"Unknown action_type: {action_type}")
            await callback.answer("Ошибка: неизвестный тип действия", show_alert=True)
        
        await state.clear()
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_grant_quick_notify_fsm: {e}")
        await callback.answer("Ошибка", show_alert=True)
        await state.clear()
```

### Fix 2: Remove Old Handler That Checks None Returns

**File:** `handlers.py`  
**Location:** Line 8279-8451 (old `callback_admin_grant_quick_notify`)

**Action:** DELETE or COMMENT OUT this handler - it's replaced by:
- `callback_admin_grant_minutes_notify` (for minutes, FSM-free)
- `callback_admin_grant_quick_notify_fsm` (for days/1_year, FSM-based)

**Reason:** The old handler:
- Checks for None returns: `if not expires_at or not vpn_key: raise Exception`
- Assumes grant happens in notify handler (wrong for minutes, which executes first)
- Creates confusion about when grant executes

---

## 4. Refactoring Plan

### Phase 1: Extract Admin Handlers (Low Risk)

**Goal:** Move all admin handlers to `app/handlers/admin.py`

**Steps:**
1. Create `app/handlers/admin.py`
2. Move admin handlers (lines ~1236-8600, ~7364 lines)
3. Import admin router in main `handlers.py`
4. Test admin flows

**Admin Handler Groups:**
- Admin menu/dashboard (lines ~1236-1300)
- Admin stats/analytics (lines ~6000-7200)
- Admin user management (lines ~1400-1500, ~7700-8600)
- Admin grant/revoke (lines ~7897-8600)
- Admin export (lines ~1416-1500)
- Admin broadcast (lines ~1300-1450)
- Admin referral stats (lines ~6400-7000)

**Estimated Lines:** ~3000 lines of admin handlers

### Phase 2: Thin Controllers (Medium Risk)

**Goal:** Move business logic to service layer

**Current Issues:**
- Handlers contain database queries directly
- Handlers contain business logic (grant calculations, notifications)
- Handlers mix presentation and business logic

**Target Structure:**
```python
# handlers.py (thin)
@router.callback_query(F.data == "admin:grant_days:*")
async def callback_admin_grant_days(callback, state, bot):
    user_id, days = parse_callback(callback.data)
    result = await admin_service.grant_access_days(user_id, days, callback.from_user.id)
    if result.success:
        await show_notify_choice(callback, user_id, days)
    else:
        await show_error(callback, result.error)

# app/services/admin/grant.py (business logic)
async def grant_access_days(user_id, days, admin_id):
    try:
        await database.admin_grant_access_atomic(...)
        return GrantResult(success=True)
    except Exception as e:
        return GrantResult(success=False, error=str(e))
```

**Affected Handlers:**
- All admin grant/revoke handlers
- Admin user management handlers
- Admin export handlers

---

## 5. Code Patches

### Patch 1: Fix None Return Check in Old Handler (if keeping it temporarily)

**File:** `handlers.py`  
**Location:** Line ~8315, ~8360, ~8406

**Change:**
```python
# OLD (raises exception on None):
if not expires_at or not vpn_key:
    raise Exception(f"admin_grant_access_atomic returned None")

# NEW (treat as side-effect):
try:
    await database.admin_grant_access_atomic(...)
    # If no exception → success (don't check return value)
except Exception as e:
    logger.exception(f"Grant failed: {e}")
    # Handle error
```

---

## 6. Summary of Required Changes

### Critical Fixes (Must Do):
1. ✅ Add handler for `admin:notify:yes` and `admin:notify:no` (FSM-based)
2. ✅ Remove or fix old `callback_admin_grant_quick_notify` that checks None returns
3. ✅ Ensure all grant handlers treat atomic functions as side-effects

### Recommended Fixes:
4. Extract admin handlers to separate module
5. Make handlers thin controllers
6. Move business logic to service layer

### Testing Checklist:
- [ ] Admin grant 1 day → notify yes → user receives message
- [ ] Admin grant 1 day → notify no → no message sent
- [ ] Admin grant 7 days → notify yes → user receives message
- [ ] Admin grant 14 days → notify yes → user receives message
- [ ] Admin grant 1 year → notify yes → user receives message
- [ ] Admin grant 10 minutes → notify yes → user receives message
- [ ] All grant flows complete without exceptions
- [ ] No "Unhandled callback_query" warnings in logs

---

## 7. File Structure After Refactoring

```
handlers.py (main, ~7000 lines)
  ├── User handlers (~2000 lines)
  ├── Payment handlers (~1500 lines)
  ├── Subscription handlers (~1000 lines)
  ├── Trial handlers (~500 lines)
  └── Import admin router

app/handlers/
  └── admin.py (~3000 lines)
      ├── Admin menu/dashboard
      ├── Admin stats/analytics
      ├── Admin user management
      ├── Admin grant/revoke
      └── Admin export/broadcast
```

---

## 8. Backward Compatibility

**No Breaking Changes:**
- All callback_data patterns remain the same
- All handler signatures remain the same
- Only internal structure changes (extraction to modules)
- Service layer additions are additive

**Migration Path:**
1. Add new handlers (backward compatible)
2. Extract admin handlers (no API changes)
3. Refactor to thin controllers (gradual, service by service)
