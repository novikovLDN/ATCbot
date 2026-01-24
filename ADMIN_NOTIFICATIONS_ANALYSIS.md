# admin_notifications.py Analysis & Refactoring Plan

## Executive Summary

**Current State:**
- 3 admin notification functions (degraded, recovered, pending activations)
- No unified entry point
- Handlers bypass module in 1+ critical place (corporate access request)
- User notifications sent directly (25+ places in handlers.py)
- Inconsistent error handling

**Goals:**
- Single entry-point for admin notifications
- Single entry-point for user notifications
- Explicit delivery attempts with observability
- Failures logged but don't crash handlers

---

## 1. Current admin_notifications.py Structure

### Existing Functions:

1. `notify_admin_degraded_mode(bot: Bot)` - Line 27
   - ✅ Has error handling (logs, doesn't raise)
   - ✅ Has spam protection (global flag)
   - ✅ Logs delivery attempts

2. `notify_admin_recovered(bot: Bot)` - Line 72
   - ✅ Has error handling (logs, doesn't raise)
   - ✅ Has spam protection (global flag)
   - ✅ Logs delivery attempts

3. `notify_admin_pending_activations(bot: Bot, pending_count: int, oldest_pending: list)` - Line 123
   - ✅ Has error handling (logs, doesn't raise)
   - ✅ Has spam protection (cooldown)
   - ✅ Logs delivery attempts

**Common Pattern:**
- All functions have try/except
- All log errors but don't raise
- All use `parse_mode=None` to avoid parse errors
- All log successful delivery

---

## 2. Bypasses Found

### Admin Notification Bypass:

**Location:** `handlers.py` line 3052  
**Context:** Corporate access request notification  
**Issue:** Direct `bot.send_message(config.ADMIN_TELEGRAM_ID, ...)` instead of using module

```python
# Current (bypasses module):
try:
    await bot.send_message(
        config.ADMIN_TELEGRAM_ID,
        admin_message,
        parse_mode=None
    )
    admin_notified = True
except Exception as e:
    logger.critical(f"Failed to send corporate access request to admin: {e}")
    admin_notified = False
```

**Should use:** Unified admin notification function

---

### User Notification Bypasses:

**Count:** 25+ direct `bot.send_message` calls to users  
**Examples:**
- Line 8271: Admin grant custom notification
- Line 8334: Admin grant minutes notification
- Line 8463: Admin grant 1 year notification
- Line 8621: Admin revoke notification
- Line 9199: Admin credit balance notification
- And many more...

**Issue:** No unified service for user notifications

---

## 3. Proposed Solution

### Part 1: Unified Admin Notification Entry Point

**Function:** `send_admin_notification(bot: Bot, message: str, notification_type: str, **kwargs) -> bool`

**Features:**
- Single entry point for all admin notifications
- Explicit delivery attempt logging
- Error handling (logs, doesn't raise)
- Returns success/failure status
- Supports different notification types (degraded, recovered, pending, custom)

### Part 2: Unified User Notification Entry Point

**Function:** `send_user_notification(bot: Bot, user_id: int, message: str, notification_type: str, parse_mode: Optional[str] = None, reply_markup: Optional[InlineKeyboardMarkup] = None, **kwargs) -> bool`

**Features:**
- Single entry point for all user notifications
- Explicit delivery attempt logging
- Error handling (logs, doesn't raise)
- Returns success/failure status
- Supports different notification types (grant, revoke, payment, etc.)

---

## 4. Exact Code Changes

### Change 1: Add Unified Entry Points to admin_notifications.py

**File:** `admin_notifications.py`  
**Location:** After line 197

```python
from typing import Optional, Dict, Any
from aiogram.types import InlineKeyboardMarkup


async def send_admin_notification(
    bot: Bot,
    message: str,
    notification_type: str = "custom",
    parse_mode: Optional[str] = None,
    **kwargs
) -> bool:
    """
    Unified entry point for sending admin notifications.
    
    This is a first-class notification service that:
    - Logs all delivery attempts explicitly
    - Handles errors gracefully (logs but doesn't crash)
    - Returns success/failure status
    - Makes delivery attempts observable
    
    Args:
        bot: Telegram bot instance
        message: Notification message text
        notification_type: Type of notification (for logging/observability)
                          Examples: "degraded_mode", "recovered", "pending_activations", 
                                   "corporate_access_request", "custom"
        parse_mode: Parse mode for message (None, "HTML", "Markdown")
        **kwargs: Additional arguments passed to bot.send_message
    
    Returns:
        bool: True if notification sent successfully, False otherwise
    
    Never raises exceptions - all errors are logged and handled gracefully.
    """
    if not config.ADMIN_TELEGRAM_ID:
        logger.warning(f"ADMIN_NOTIFICATION_SKIPPED [type={notification_type}, reason=admin_id_not_configured]")
        return False
    
    try:
        logger.info(f"ADMIN_NOTIFICATION_ATTEMPT [type={notification_type}, admin_id={config.ADMIN_TELEGRAM_ID}]")
        
        await bot.send_message(
            config.ADMIN_TELEGRAM_ID,
            message,
            parse_mode=parse_mode or None,  # Default to None to avoid parse errors
            **kwargs
        )
        
        logger.info(f"ADMIN_NOTIFICATION_SENT [type={notification_type}, admin_id={config.ADMIN_TELEGRAM_ID}]")
        return True
        
    except Exception as e:
        logger.error(
            f"ADMIN_NOTIFICATION_FAILED [type={notification_type}, admin_id={config.ADMIN_TELEGRAM_ID}, "
            f"error={type(e).__name__}: {str(e)[:100]}]"
        )
        logger.exception(f"Admin notification delivery failed (non-fatal): {e}")
        return False


async def send_user_notification(
    bot: Bot,
    user_id: int,
    message: str,
    notification_type: str = "custom",
    parse_mode: Optional[str] = None,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    **kwargs
) -> bool:
    """
    Unified entry point for sending user notifications.
    
    This is a first-class notification service that:
    - Logs all delivery attempts explicitly
    - Handles errors gracefully (logs but doesn't crash)
    - Returns success/failure status
    - Makes delivery attempts observable
    
    Args:
        bot: Telegram bot instance
        user_id: Telegram user ID
        message: Notification message text
        notification_type: Type of notification (for logging/observability)
                          Examples: "admin_grant", "admin_revoke", "payment_approved",
                                   "subscription_renewed", "corporate_access_confirmation", "custom"
        parse_mode: Parse mode for message (None, "HTML", "Markdown")
        reply_markup: Optional inline keyboard
        **kwargs: Additional arguments passed to bot.send_message
    
    Returns:
        bool: True if notification sent successfully, False otherwise
    
    Never raises exceptions - all errors are logged and handled gracefully.
    """
    try:
        logger.info(f"USER_NOTIFICATION_ATTEMPT [type={notification_type}, user_id={user_id}]")
        
        await bot.send_message(
            user_id,
            message,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            **kwargs
        )
        
        logger.info(f"USER_NOTIFICATION_SENT [type={notification_type}, user_id={user_id}]")
        return True
        
    except Exception as e:
        # Handle specific Telegram errors gracefully
        error_type = type(e).__name__
        error_msg = str(e)
        
        # User blocked bot or deleted account
        if "blocked" in error_msg.lower() or "chat not found" in error_msg.lower():
            logger.warning(
                f"USER_NOTIFICATION_SKIPPED [type={notification_type}, user_id={user_id}, "
                f"reason=user_unreachable, error={error_type}]"
            )
        else:
            logger.error(
                f"USER_NOTIFICATION_FAILED [type={notification_type}, user_id={user_id}, "
                f"error={error_type}: {error_msg[:100]}]"
            )
            logger.exception(f"User notification delivery failed (non-fatal): {e}")
        
        return False
```

### Change 2: Refactor Existing Functions to Use Unified Entry Point

**File:** `admin_notifications.py`  
**Location:** Lines 27-69, 72-109, 123-196

```python
# Update notify_admin_degraded_mode to use unified entry point
async def notify_admin_degraded_mode(bot: Bot):
    """
    Уведомить администратора о том, что бот работает в деградированном режиме
    
    Args:
        bot: Экземпляр Telegram бота
        
    Отправляет сообщение только один раз при переходе в деградированный режим.
    """
    global _degraded_notification_sent
    
    if _degraded_notification_sent:
        return
    
    message = (
        "⚠️ **БОТ РАБОТАЕТ В ДЕГРАДИРОВАННОМ РЕЖИМЕ**\n\n"
        "База данных недоступна.\n\n"
        "• Бот запущен и отвечает на команды\n"
        "• Критические операции блокируются\n"
        "• Пользователи получают сообщения о временной недоступности\n\n"
        "Бот будет автоматически пытаться восстановить соединение с БД каждые 30 секунд.\n\n"
        "Проверьте:\n"
        "• Доступность PostgreSQL\n"
        "• Правильность DATABASE_URL\n"
        "• Сетевые настройки"
    )
    
    success = await send_admin_notification(
        bot=bot,
        message=message,
        notification_type="degraded_mode",
        parse_mode=None
    )
    
    if success:
        _degraded_notification_sent = True


# Similar updates for notify_admin_recovered and notify_admin_pending_activations
```

### Change 3: Fix Corporate Access Request Bypass

**File:** `handlers.py`  
**Location:** Line 3051-3060

**Change:**
```python
# OLD (bypasses module):
try:
    await bot.send_message(
        config.ADMIN_TELEGRAM_ID,
        admin_message,
        parse_mode=None
    )
    admin_notified = True
except Exception as e:
    logger.critical(f"Failed to send corporate access request to admin: {e}")
    admin_notified = False

# NEW (uses unified service):
import admin_notifications
admin_notified = await admin_notifications.send_admin_notification(
    bot=bot,
    message=admin_message,
    notification_type="corporate_access_request",
    parse_mode=None
)
```

### Change 4: Update User Notifications to Use Unified Service

**File:** `handlers.py`  
**Multiple locations (examples)**

**Example 1: Line 8334 (admin grant minutes)**
```python
# OLD:
try:
    user_text = f"Администратор выдал вам доступ на {minutes} минут"
    await bot.send_message(user_id, user_text)
    logger.info(f"NOTIFICATION_SENT [type=admin_grant, user_id={user_id}, minutes={minutes}]")
except Exception as e:
    logger.exception(f"Error sending notification to user {user_id}: {e}")

# NEW:
import admin_notifications
success = await admin_notifications.send_user_notification(
    bot=bot,
    user_id=user_id,
    message=f"Администратор выдал вам доступ на {minutes} минут",
    notification_type="admin_grant_minutes"
)
if success:
    logger.info(f"NOTIFICATION_SENT [type=admin_grant, user_id={user_id}, minutes={minutes}]")
```

**Example 2: Line 8463 (admin grant 1 year)**
```python
# OLD:
try:
    user_text = "Администратор выдал вам доступ на 1 год"
    await bot.send_message(user_id, user_text)
    logger.info(f"NOTIFICATION_SENT [type=admin_grant, user_id={user_id}, duration=1_year]")
except Exception as e:
    logger.exception(f"Error sending notification: {e}")

# NEW:
import admin_notifications
success = await admin_notifications.send_user_notification(
    bot=bot,
    user_id=user_id,
    message="Администратор выдал вам доступ на 1 год",
    notification_type="admin_grant_1_year"
)
if success:
    logger.info(f"NOTIFICATION_SENT [type=admin_grant, user_id={user_id}, duration=1_year]")
```

---

## 5. Notification Types Catalog

### Admin Notification Types:
- `degraded_mode` - Bot entered degraded mode
- `recovered` - Bot recovered from degraded mode
- `pending_activations` - Pending VPN activations alert
- `corporate_access_request` - New corporate access request
- `custom` - Generic admin notification

### User Notification Types:
- `admin_grant_days` - Admin granted access (N days)
- `admin_grant_minutes` - Admin granted access (N minutes)
- `admin_grant_1_year` - Admin granted access (1 year)
- `admin_grant_custom` - Admin granted custom duration access
- `admin_revoke` - Admin revoked access
- `corporate_access_confirmation` - Corporate access request confirmed
- `payment_approved` - Payment approved
- `subscription_renewed` - Subscription renewed
- `custom` - Generic user notification

---

## 6. Observability Improvements

### Log Patterns:

**Admin Notifications:**
- `ADMIN_NOTIFICATION_ATTEMPT [type=..., admin_id=...]` - Delivery attempt started
- `ADMIN_NOTIFICATION_SENT [type=..., admin_id=...]` - Delivery successful
- `ADMIN_NOTIFICATION_FAILED [type=..., admin_id=..., error=...]` - Delivery failed
- `ADMIN_NOTIFICATION_SKIPPED [type=..., reason=...]` - Delivery skipped (e.g., admin_id not configured)

**User Notifications:**
- `USER_NOTIFICATION_ATTEMPT [type=..., user_id=...]` - Delivery attempt started
- `USER_NOTIFICATION_SENT [type=..., user_id=...]` - Delivery successful
- `USER_NOTIFICATION_FAILED [type=..., user_id=..., error=...]` - Delivery failed
- `USER_NOTIFICATION_SKIPPED [type=..., user_id=..., reason=...]` - Delivery skipped (e.g., user blocked bot)

---

## 7. Migration Strategy

### Phase 1: Add Unified Functions (No Breaking Changes)
- Add `send_admin_notification` and `send_user_notification` to admin_notifications.py
- Refactor existing functions to use unified entry points
- Test existing functionality

### Phase 2: Migrate Handlers (Incremental)
- Fix corporate access request bypass (critical)
- Migrate admin grant/revoke notifications (high priority)
- Migrate other user notifications (medium priority)

### Phase 3: Cleanup (Optional)
- Remove direct `bot.send_message` calls where possible
- Ensure all notifications go through unified service

---

## 8. Backward Compatibility

**No Breaking Changes:**
- Existing functions (`notify_admin_degraded_mode`, etc.) remain
- They now use unified entry point internally
- All existing callers continue to work
- New unified functions are additive

**Benefits:**
- Consistent error handling
- Observable delivery attempts
- Single source of truth for notification logic
- Easier to add features (retry, rate limiting, etc.)
