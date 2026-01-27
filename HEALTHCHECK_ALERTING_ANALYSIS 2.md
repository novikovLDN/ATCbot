# Healthcheck & Alerting Components Analysis

## Executive Summary

**Issues Found:** 2 critical issues  
**Critical Risks:** Alert spam in healthcheck  
**Severity Levels:** 1 issue (WARNING should be CRITICAL)  
**Incident Lifecycle:** ✅ Correct

---

## 1. Severity Levels Analysis

### ✅ Correct Severity Usage:

1. **admin_notifications.py:**
   - ✅ `logger.info()` for notification attempts
   - ✅ `logger.warning()` for skipped notifications
   - ✅ `logger.error()` for failed notifications
   - ✅ Appropriate levels

2. **app/core/alerts.py:**
   - ✅ `AlertSeverity.PAGE` → `logging.CRITICAL` (line 326)
   - ✅ `AlertSeverity.TICKET` → `logging.WARNING` (line 327)
   - ✅ `AlertSeverity.INFO` → `logging.INFO` (line 328)
   - ✅ Proper severity mapping

### ❌ Issue 1: Incorrect Severity in healthcheck.py

**Location:** `healthcheck.py` line 328

**Problem:**
- Health check alerts use `logger.warning()` for critical system failures
- Should use `logger.error()` or `logging.CRITICAL` for unavailable system
- WARNING is too low for system-wide failures

**Current Code:**
```python
await bot.send_message(config.ADMIN_TELEGRAM_ID, alert_text)
logger.warning(f"Health check alert sent to admin: {alert_text}")  # ❌ Should be ERROR/CRITICAL
```

**Fix Required:**
- Use `logger.error()` or `logging.CRITICAL` for health check alerts
- Differentiate severity based on system state (UNAVAILABLE vs DEGRADED)

---

## 2. Alert Spam Prevention Analysis

### ✅ Correct Spam Prevention:

1. **admin_notifications.py:**
   - ✅ `_degraded_notification_sent` flag (line 26, 47-48)
   - ✅ `_recovered_notification_sent` flag (line 27, 87-88)
   - ✅ `PENDING_NOTIFICATION_COOLDOWN_SECONDS = 3600` (line 32, 143-146)
   - ✅ Flags reset on startup (line 110-118) - correct behavior

2. **app/core/alerts.py:**
   - ✅ `_alert_state` tracking (line 60, 93-95)
   - ✅ Cooldowns: unavailable (120s), degraded (600s), recovery_failed (300s), slo_breach (600s)
   - ✅ Suppression during recovery cooldown (line 87-89, 139-141)

### ❌ Issue 2: No Spam Protection in healthcheck.py

**Location:** `healthcheck.py` lines 336-359

**Problem:**
- `health_check_task()` sends alerts every 10 minutes if `not all_ok`
- No state tracking or cooldown
- Will spam admin every 10 minutes during outages
- `send_health_alert()` has no spam protection

**Current Code:**
```python
async def health_check_task(bot: Bot):
    while True:
        all_ok, messages = await perform_health_check()
        if not all_ok:
            await send_health_alert(bot, messages)  # ❌ No spam protection
            logger.warning(f"Health check failed: {messages}")
        await asyncio.sleep(10 * 60)  # Every 10 minutes
```

**Impact:**
- During 1-hour outage: 6 alert messages
- During 24-hour outage: 144 alert messages
- Admin notification spam

**Fix Required:**
- Add state tracking for health check alerts
- Add cooldown (e.g., 1 hour minimum between alerts)
- Only send alert on state transition (unavailable → unavailable is not a transition)
- Use incident context to track if alert already sent

---

## 3. Incident Lifecycle Handling

### ✅ Correct Incident Lifecycle:

1. **healthcheck.py (Lines 288-303):**
   - ✅ Starts incident context when system becomes unavailable (line 289-294)
   - ✅ Clears incident context when system recovers (line 297-303)
   - ✅ Logs incident ID for correlation
   - ✅ Proper lifecycle management

2. **app/core/audit_policy.py:**
   - ✅ `IncidentContext` class tracks incident ID and start time
   - ✅ `start_incident()` generates UUID
   - ✅ `clear_incident()` resets state
   - ✅ `get_correlation_id()` returns incident ID if active

3. **app/core/alerts.py:**
   - ✅ Suppresses alerts during recovery cooldown
   - ✅ Tracks alert state to prevent spam
   - ✅ Clears suppressed alerts when system recovers

### ⚠️ Issue: Health Check Alerts Not Integrated with Incident Context

**Location:** `healthcheck.py` line 344

**Problem:**
- `send_health_alert()` is called without checking incident context
- Could send duplicate alerts if incident already has alert sent
- Not using incident context for correlation

**Fix Required:**
- Check incident context before sending alert
- Track if alert was sent for current incident
- Use incident ID in alert message

---

## 4. Exact Code Fixes

### Fix 1: Add Spam Protection to Health Check Alerts

**File:** `healthcheck.py`  
**Location:** Lines 314-333, 336-359

**Change:**
```python
# Add state tracking at module level (after line 38)
_health_alert_state: Dict[str, datetime] = {}  # alert_key -> last_sent_at
HEALTH_ALERT_COOLDOWN_SECONDS = 3600  # 1 hour minimum between alerts

async def send_health_alert(bot: Bot, messages: List[str]):
    """Отправить алерт администратору о проблемах с системой
    
    Args:
        bot: Экземпляр бота
        messages: Список сообщений о проблемах
    
    NOTE: Read-only healthcheck - NO INSERT/UPDATE, NO audit_log writes
    NOTE: Spam protection - only sends once per cooldown period
    """
    global _health_alert_state
    
    # Check cooldown to prevent spam
    now = datetime.utcnow()
    alert_key = "health_check_failed"
    last_sent = _health_alert_state.get(alert_key)
    
    if last_sent and (now - last_sent).total_seconds() < HEALTH_ALERT_COOLDOWN_SECONDS:
        logger.debug(
            f"Health check alert skipped (cooldown active, "
            f"last_sent={last_sent.isoformat()}, "
            f"cooldown={HEALTH_ALERT_COOLDOWN_SECONDS}s)"
        )
        return
    
    # Check incident context - only send if new incident or alert not sent for this incident
    try:
        from app.core.audit_policy import get_incident_context
        incident_context = get_incident_context()
        incident_id = incident_context.get_incident_id()
        
        # If incident exists and we already sent alert for this incident, skip
        if incident_id and alert_key in _health_alert_state:
            logger.debug(f"Health check alert skipped (already sent for incident {incident_id})")
            return
    except Exception:
        # If incident context fails, continue anyway (non-blocking)
        pass
    
    try:
        alert_text = "🚨 Health Check Alert\n\nОбнаружены проблемы:\n\n"
        alert_text += "\n".join(f"• {msg}" for msg in messages)
        
        await bot.send_message(config.ADMIN_TELEGRAM_ID, alert_text)
        logger.error(f"Health check alert sent to admin: {alert_text}")  # Changed from warning to error
        
        # Update state tracking
        _health_alert_state[alert_key] = now
        
    except Exception as e:
        logger.error(f"Error sending health check alert to admin: {e}", exc_info=True)

# Update health_check_task to clear alert state on recovery
async def health_check_task(bot: Bot):
    """Фоновая задача для health-check (выполняется каждые 10 минут)"""
    global _health_alert_state
    previous_all_ok = None
    
    while True:
        try:
            all_ok, messages = await perform_health_check()
            
            # Clear alert state if system recovered
            if previous_all_ok is False and all_ok is True:
                _health_alert_state.clear()
                logger.info("Health check recovered - alert state cleared")
            
            if not all_ok:
                # Only send alert if state changed or cooldown expired
                await send_health_alert(bot, messages)
                logger.error(f"Health check failed: {messages}")  # Changed from warning to error
            else:
                logger.info("Health check passed: all components OK")
            
            previous_all_ok = all_ok
                
        except Exception as e:
            logger.exception(f"Error in health_check_task: {e}")
            # При критической ошибке тоже отправляем алерт (with spam protection)
            try:
                error_msg = f"Критическая ошибка health-check: {str(e)}"
                await send_health_alert(bot, [error_msg])
            except:
                pass  # Не падаем, если не можем отправить алерт
        
        # Ждем 10 минут до следующей проверки
        await asyncio.sleep(10 * 60)  # 10 минут в секундах
```

---

### Fix 2: Fix Severity Levels

**File:** `healthcheck.py`  
**Location:** Line 328, 345

**Change:**
```python
# Line 328: Change from warning to error
logger.error(f"Health check alert sent to admin: {alert_text}")  # ✅ ERROR for critical alerts

# Line 345: Change from warning to error
logger.error(f"Health check failed: {messages}")  # ✅ ERROR for system failures
```

---

## 5. Summary of Issues

| Issue | Severity | Location | Fix |
|-------|----------|----------|-----|
| No spam protection in health check alerts | Critical | Line 344 | Add cooldown and state tracking |
| Incorrect severity level (WARNING instead of ERROR) | Medium | Line 328, 345 | Change to ERROR |

---

## 6. Correctness Confirmation

### ✅ Severity Levels: NEEDS FIX
- ❌ Health check alerts use WARNING (should be ERROR/CRITICAL)
- ✅ Admin notifications use appropriate levels
- ✅ Alert system has proper severity mapping

### ❌ Alert Spam Prevention: NEEDS FIX
- ❌ Health check alerts have no spam protection
- ✅ Admin notifications have spam protection
- ✅ Alert rules have spam protection

### ✅ Incident Lifecycle: CORRECT
- ✅ Incident context properly tracked
- ✅ Incident start/clear logic correct
- ✅ Correlation IDs used correctly

---

## 7. Suggested Improvements (Non-Critical)

### Improvement 1: Integrate Health Check with Alert Rules

**Suggestion:**
- Use `app/core/alerts.py` for health check alerts instead of direct `send_health_alert()`
- Leverage existing spam protection and severity mapping
- Consistent alerting across system

**Benefit:**
- Unified alerting system
- Better spam protection
- Consistent severity levels

### Improvement 2: State-Based Alert Sending

**Suggestion:**
- Only send alert on state transition (healthy → unavailable, unavailable → unavailable is not a transition)
- Track previous state in `health_check_task()`
- Send alert only when state changes

**Benefit:**
- No spam during persistent outages
- Alerts only on state changes
- Better operator experience

---

## 8. Testing Recommendations

1. **Alert Spam Test:**
   - Simulate 1-hour DB outage
   - Verify: Only 1 alert sent (not 6)
   - Verify: Cooldown prevents spam

2. **Severity Test:**
   - Check logs for health check alerts
   - Verify: ERROR level (not WARNING)
   - Verify: Appropriate for system failures

3. **Incident Lifecycle Test:**
   - Simulate system unavailable → available
   - Verify: Incident context started/cleared
   - Verify: Alert state cleared on recovery

4. **State Transition Test:**
   - Simulate healthy → unavailable → unavailable
   - Verify: Alert sent only on first transition
   - Verify: No spam during persistent outage
