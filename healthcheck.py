"""Модуль для health-check основных компонентов системы"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Tuple, List, Optional
from aiogram import Bot
import database
import config
from app.core.system_state import (
    SystemState,
    ComponentStatus,
    healthy_component,
    degraded_component,
    unavailable_component,
)
from app.core.recovery_cooldown import (
    get_recovery_cooldown,
    ComponentName,
)
from app.core.metrics import get_metrics
from app.core.slo import get_slo
from app.core.alerts import get_alert_rules, send_alert
from app.core.performance_budget import get_performance_budget, OperationType
from app.core.cost_model import get_cost_model, CostCenter, DEFAULT_COST_THRESHOLDS
from app.core.audit_policy import (
    get_audit_policy_engine,
    get_incident_context,
    AuditEventType,
)

logger = logging.getLogger(__name__)

# Минимальное количество свободных VPN-ключей больше не используется
# VPN-ключи создаются динамически через Xray API (VLESS + REALITY)

# B4.1 - SYSTEM STATE TRANSITIONS: In-memory previous state tracking
_previous_system_state: Optional[SystemState] = None

# Alert spam protection: Track last sent time to prevent spam
_health_alert_state: dict[str, datetime] = {}  # alert_key -> last_sent_at
HEALTH_ALERT_COOLDOWN_SECONDS = 3600  # 1 hour minimum between alerts


async def check_database_connection() -> Tuple[bool, str]:
    """Проверить подключение к PostgreSQL
    
    Returns:
        Кортеж (is_ok, message) - статус проверки и сообщение
    
    NOTE: Read-only check - только SELECT, никаких INSERT/UPDATE
    """
    try:
        if not database.DB_READY:
            return False, "PostgreSQL подключение: DB not ready (degraded mode)"
        
        pool = await database.get_pool()
        if pool is None:
            return False, "PostgreSQL подключение: Pool is None"
        
        async with pool.acquire() as conn:
            # Выполняем простой запрос для проверки подключения (read-only)
            result = await conn.fetchval("SELECT 1")
            if result == 1:
                return True, "PostgreSQL подключение: OK"
            else:
                return False, "PostgreSQL подключение: Ошибка (неожиданный результат)"
    except Exception as e:
        # В STAGE/LOCAL логируем как WARNING, не ERROR
        if config.IS_STAGE or config.IS_LOCAL:
            logger.warning(f"Database connection check failed in {config.APP_ENV.upper()}: {e}")
        else:
            logger.error(f"Database connection check failed: {e}")
        return False, f"PostgreSQL подключение: Ошибка ({str(e)})"


async def check_connection_pool() -> Tuple[bool, str]:
    """Проверить доступность пула соединений
    
    Returns:
        Кортеж (is_ok, message) - статус проверки и сообщение
    """
    try:
        pool = await database.get_pool()
        if pool is None:
            return False, "Пул соединений: Не создан"
        
        # Проверяем, что пул активен
        if pool.is_closing():
            return False, "Пул соединений: Закрывается"
        
        return True, "Пул соединений: OK"
    except Exception as e:
        return False, f"Пул соединений: Ошибка ({str(e)})"


async def check_vpn_keys() -> Tuple[bool, str]:
    """Проверить доступность Xray API через реальный health-check.
    
    Returns:
        Кортеж (is_ok, message) - статус проверки и сообщение
    """
    try:
        import config
        import vpn_utils
        
        # Проверяем, что XRAY_API_URL настроен
        if not config.XRAY_API_URL or not config.XRAY_API_KEY:
            return False, "VPN API: XRAY_API_URL или XRAY_API_KEY не настроен"
        
        # Выполняем реальный health-check через XRAY API /health endpoint
        is_healthy = await vpn_utils.check_xray_health()
        if is_healthy:
            return True, "VPN API: Доступен (health-check успешен)"
        else:
            return False, "VPN API: Health-check failed (недоступен)"
    except Exception as e:
        return False, f"VPN API: Ошибка health-check ({str(e)})"


async def perform_health_check() -> Tuple[bool, list]:
    """Выполнить health-check всех компонентов
    
    PART B.4: Healthcheck MUST return DEGRADED (not FAILED) if VPN API missing.
    Return HEALTHY if DB = OK and Pool = OK.
    
    Returns:
        Кортеж (all_ok, messages) - общий статус и список сообщений
        all_ok = False ONLY if CRITICAL components (DB, Pool) are down
        VPN API missing → all_ok = True (system is HEALTHY, VPN is non-critical)
    """
    messages = []
    now = datetime.now(timezone.utc)
    
    # Проверка PostgreSQL
    db_ok, db_msg = await check_database_connection()
    messages.append(db_msg)
    
    # Проверка пула соединений
    pool_ok, pool_msg = await check_connection_pool()
    messages.append(pool_msg)
    
    # Проверка VPN-ключей (NON-CRITICAL)
    keys_ok, keys_msg = await check_vpn_keys()
    messages.append(keys_msg)
    
    # PART B.4: all_ok = True if DB and Pool are OK (VPN is non-critical)
    all_ok = db_ok and pool_ok
    
    # STEP 1.1 - RUNTIME GUARDRAILS: SystemState is constructed centrally in healthcheck
    # SystemState is a READ-ONLY snapshot of system health
    # Build SystemState based on current checks (internal computation only)
    # Database component state
    if database.DB_READY and db_ok and pool_ok:
        db_component = healthy_component(last_checked_at=now)
    else:
        # Extract error message from db_msg or pool_msg
        error_msg = db_msg if not db_ok else pool_msg
        db_component = unavailable_component(error=error_msg, last_checked_at=now)
    
    # VPN API component state
    if keys_ok:
        vpn_component = healthy_component(last_checked_at=now)
    else:
        # VPN API missing or error - degraded (not unavailable, as system can work without it)
        vpn_component = degraded_component(error=keys_msg, last_checked_at=now)
    
    # Payments component state (always healthy - no logic change)
    payments_component = healthy_component(last_checked_at=now)
    
    # Create SystemState instance (for internal use, not exposed)
    system_state = SystemState(
        database=db_component,
        vpn_api=vpn_component,
        payments=payments_component,
    )
    
    # B4.1 - SYSTEM STATE TRANSITIONS: Track state transitions (observed, not forced)
    global _previous_system_state
    if _previous_system_state is not None:
        # Check for transitions in each component
        components = [
            ("database", _previous_system_state.database, system_state.database, ComponentName.DATABASE),
            ("vpn_api", _previous_system_state.vpn_api, system_state.vpn_api, ComponentName.VPN_API),
            ("payments", _previous_system_state.payments, system_state.payments, ComponentName.PAYMENTS),
        ]
        
        recovery_cooldown = get_recovery_cooldown(cooldown_seconds=60)
        
        for comp_name, prev_comp, curr_comp, comp_enum in components:
            prev_status = prev_comp.status
            curr_status = curr_comp.status
            
            # Detect transitions
            if prev_status != curr_status:
                # D3.2 - AUDIT TRAIL HARDENING: Log system degradation transitions
                audit_policy = get_audit_policy_engine()
                incident_context = get_incident_context()
                correlation_id = incident_context.get_correlation_id()
                
                # Log transition with audit policy
                transition_data = {
                    "component": comp_name,
                    "prev_status": prev_status.value,
                    "curr_status": curr_status.value,
                    "timestamp": now.isoformat(),
                    "correlation_id": correlation_id,
                }
                
                # Sanitize for audit (no sensitive data in this case)
                sanitized_data = audit_policy.sanitize_for_audit(
                    AuditEventType.SYSTEM_DEGRADATION,
                    transition_data
                )
                
                logger.info(
                    f"[RECOVERY] component={comp_name} transitioned from {prev_status.value} to {curr_status.value} "
                    f"[INCIDENT {correlation_id}]"
                )
                
                # D3.2 - AUDIT TRAIL: Log to audit trail (if audit policy requires)
                if audit_policy.should_audit(AuditEventType.SYSTEM_DEGRADATION):
                    logger.info(
                        f"[AUDIT] SYSTEM_DEGRADATION: {sanitized_data}"
                    )
                
                # B4.2 - COOLDOWN & BACKOFF: Mark unavailable and start cooldown
                if curr_status == ComponentStatus.UNAVAILABLE:
                    recovery_cooldown.mark_unavailable(comp_enum, now)
                elif prev_status == ComponentStatus.UNAVAILABLE and curr_status != ComponentStatus.UNAVAILABLE:
                    # Component recovered from unavailable - clear cooldown after a delay
                    # Cooldown will naturally expire, but we log recovery
                    logger.info(
                        f"[RECOVERY] component={comp_name} recovered from UNAVAILABLE to {curr_status.value} "
                        f"[INCIDENT {correlation_id}]"
                    )
    
    # Update previous state for next iteration
    _previous_system_state = system_state
    
    # C1.1 - METRICS: Update metrics based on system state
    # PART E — SLO SIGNAL IDENTIFICATION: System degraded vs unavailable ratio
    # This system_state_status gauge is an SLO signal for system health.
    # Track: system_state_status = 0 (healthy), 1 (degraded), 2 (unavailable).
    # SLO: system_state != UNAVAILABLE ≥ 99.9%, DEGRADED ≤ 5% of time.
    metrics = get_metrics()
    system_state_status = 2.0 if system_state.is_unavailable else (1.0 if system_state.is_degraded else 0.0)
    metrics.set_gauge("system_state_status", system_state_status)
    
    # Update recovery and cooldown gauges
    recovery_cooldown = get_recovery_cooldown()
    recovery_in_progress = any(
        recovery_cooldown.is_in_cooldown(comp, now)
        for comp in [ComponentName.DATABASE, ComponentName.VPN_API, ComponentName.PAYMENTS]
    )
    metrics.set_gauge("recovery_in_progress", 1.0 if recovery_in_progress else 0.0)
    metrics.set_gauge("cooldown_active", 1.0 if recovery_in_progress else 0.0)
    
    # C2.2 - ALERT RULES: Evaluate alert rules (does not affect return value)
    try:
        alert_rules = get_alert_rules()
        alerts = alert_rules.evaluate_all_rules(system_state, recovery_attempts=0)
        for alert in alerts:
            # Log alerts (actual sending would be done by health_check_task)
            send_alert(alert)
    except Exception as e:
        # Alert evaluation must not break health check
        logger.debug(f"Error evaluating alerts: {e}")
    
    # D1.1 - LATENCY BUDGETING: Check performance budgets (observability only)
    try:
        performance_budget = get_performance_budget()
        budget_results = performance_budget.check_all_budgets()
        # Log budget violations (for observability, not blocking)
        for op_type, result in budget_results.items():
            if result.get("is_compliant") is False:
                logger.warning(
                    f"[PERFORMANCE] Budget violation: {op_type.value} "
                    f"P95={result.get('actual_p95_ms')}ms > budget={result.get('budget_p95_ms')}ms"
                )
    except Exception as e:
        logger.debug(f"Error checking performance budgets: {e}")
    
    # D2.3 - COST ANOMALY DETECTION: Check for cost anomalies
    try:
        cost_model = get_cost_model()
        cost_model.check_and_alert_cost_anomalies()
    except Exception as e:
        logger.debug(f"Error checking cost anomalies: {e}")
    
    # D3.3 - INCIDENT READINESS: Track system degradation transitions
    try:
        incident_context = get_incident_context()
        audit_policy = get_audit_policy_engine()
        
        # Start incident context if system becomes unavailable
        if system_state.is_unavailable and incident_context.get_incident_id() is None:
            incident_id = incident_context.start_incident()
            logger.warning(
                f"[INCIDENT] Started incident context: {incident_id} "
                f"(system_state=UNAVAILABLE)"
            )
        
        # Clear incident context if system recovers
        if not system_state.is_unavailable and incident_context.get_incident_id() is not None:
            incident_id = incident_context.get_incident_id()
            incident_context.clear_incident()
            logger.info(
                f"[INCIDENT] Cleared incident context: {incident_id} "
                f"(system_state recovered)"
            )
    except Exception as e:
        logger.debug(f"Error managing incident context: {e}")
    
    # Use SystemState properties internally but preserve external behavior
    # all_ok is already computed above, but we can verify with system_state
    # Note: We preserve the original all_ok computation to maintain exact behavior
    
    return all_ok, messages


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
    now = datetime.now(timezone.utc)
    alert_key = "health_check_failed"
    last_sent = _health_alert_state.get(alert_key)
    
    if last_sent and (now - last_sent).total_seconds() < HEALTH_ALERT_COOLDOWN_SECONDS:
        logger.debug(
            f"Health check alert skipped (cooldown active, "
            f"last_sent={last_sent.isoformat()}, "
            f"cooldown={HEALTH_ALERT_COOLDOWN_SECONDS}s)"
        )
        return
    
    # Check incident context - track alerts per incident to prevent duplicates
    incident_id = None
    incident_alert_key = alert_key
    try:
        from app.core.audit_policy import get_incident_context
        incident_context = get_incident_context()
        incident_id = incident_context.get_incident_id()
        
        # Track incident-specific alerts: incident_id -> last_sent_at
        if incident_id:
            incident_alert_key = f"{alert_key}:{incident_id}"
            
            # If we already sent alert for this specific incident, skip
            if incident_alert_key in _health_alert_state:
                logger.debug(f"Health check alert skipped (already sent for incident {incident_id})")
                return
    except Exception:
        # If incident context fails, continue anyway (non-blocking)
        pass
    
    try:
        alert_text = "🚨 Health Check Alert\n\nОбнаружены проблемы:\n\n"
        alert_text += "\n".join(f"• {msg}" for msg in messages)
        
        await bot.send_message(config.ADMIN_TELEGRAM_ID, alert_text)
        logger.error(f"Health check alert sent to admin: {alert_text}")  # ERROR for critical alerts
        
        # Update state tracking (both general and incident-specific)
        _health_alert_state[alert_key] = now
        if incident_id:
            _health_alert_state[incident_alert_key] = now
        
        # НЕ записываем в audit_log - healthcheck должен быть read-only
        # Если audit_log таблица отсутствует, это не должно ломать healthcheck
    except Exception as e:
        logger.error(f"Error sending health check alert to admin: {e}", exc_info=True)


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
                # Only send alert if state changed or cooldown expired (spam protection in send_health_alert)
                await send_health_alert(bot, messages)
                logger.error(f"Health check failed: {messages}")  # ERROR for system failures
            else:
                logger.info("Health check passed: all components OK")
            
            previous_all_ok = all_ok
                
        except asyncio.CancelledError:
            logger.info("Healthcheck task cancelled")
            break
        except Exception as e:
            logger.exception(f"Error in health_check_task: {e}")
            # При критической ошибке тоже отправляем алерт (with spam protection)
            try:
                error_msg = f"Критическая ошибка health-check: {str(e)}"
                await send_health_alert(bot, [error_msg])
            except Exception as alert_error:
                logger.debug(f"Failed to send health check alert: {alert_error}")
        
        # Ждем 10 минут до следующей проверки
        await asyncio.sleep(10 * 60)  # 10 минут в секундах

