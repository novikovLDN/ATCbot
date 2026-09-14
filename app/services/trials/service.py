"""
Trial Service Layer

This module provides business logic for trial subscriptions, notifications, and expiration.
It acts as a thin wrapper around database operations, providing a clean interface
for handlers and background tasks while keeping business logic separate from Telegram-specific code.

All functions are pure business logic:
- No aiogram imports
- No logging
- No Telegram calls
- Pure business logic only
"""

import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta, timezone
import database
from app.services import provisioning_flags

logger = logging.getLogger(__name__)


# ====================================================================================
# Trial Availability
# ====================================================================================

async def is_trial_available(telegram_id: int) -> bool:
    """
    Check if trial is available for a user.
    
    Trial is available if:
    - trial_used_at IS NULL
    - No active subscription
    - No paid subscriptions in history (source='payment')
    
    Args:
        telegram_id: Telegram ID of the user
        
    Returns:
        True if trial is available, False otherwise
    """
    return await database.is_trial_available(telegram_id)


# ====================================================================================
# Domain Exceptions
# ====================================================================================

class TrialServiceError(Exception):
    """Base exception for trial service errors"""
    pass


class TrialExpiredError(TrialServiceError):
    """Raised when trial has already expired"""
    pass


class InvalidTrialStateError(TrialServiceError):
    """Raised when trial state is invalid for the operation"""
    pass


# ====================================================================================
# Trial Expiration Logic
# ====================================================================================

async def is_trial_expired(
    telegram_id: int,
    trial_expires_at: datetime,
    now: datetime
) -> bool:
    """
    Check if trial has expired.
    
    Args:
        telegram_id: Telegram ID of the user
        trial_expires_at: Trial expiration timestamp
        now: Current timestamp
        
    Returns:
        True if trial has expired, False otherwise
    """
    if not trial_expires_at:
        return True
    
    return trial_expires_at <= now


async def should_expire_trial(
    telegram_id: int,
    trial_expires_at: datetime,
    now: datetime,
    conn
) -> Tuple[bool, Optional[str]]:
    """
    Determine if trial should be expired and processed.
    
    Checks:
    - Trial has expired (trial_expires_at <= now)
    - Trial expired within last 24 hours (to prevent duplicate processing)
    - User has active trial subscription
    
    Args:
        telegram_id: Telegram ID of the user
        trial_expires_at: Trial expiration timestamp
        now: Current timestamp
        conn: Database connection
        
    Returns:
        Tuple[bool, Optional[str]]:
        - (True, None) if trial should be expired
        - (False, reason) if trial should not be expired
    """
    if not trial_expires_at:
        return (False, "trial_expires_at_is_none")
    
    # Check if trial has expired
    if trial_expires_at > now:
        return (False, "trial_not_expired")
    
    # Check if trial expired within last 24 hours (prevent duplicate processing)
    if trial_expires_at <= now - timedelta(hours=24):
        return (False, "trial_expired_too_long_ago")
    
    # Check if user has active trial subscription
    subscription = await conn.fetchrow("""
        SELECT id, source, status, expires_at, uuid
        FROM subscriptions
        WHERE telegram_id = $1
        AND source = 'trial'
        AND status = 'active'
        LIMIT 1
    """, telegram_id)
    
    if not subscription:
        return (False, "no_active_trial_subscription")
    
    return (True, None)


# ====================================================================================
# Trial Notification Logic
# ====================================================================================

def calculate_trial_timing(
    trial_expires_at: datetime,
    now: datetime
) -> Dict[str, float]:
    """
    Calculate trial timing metrics.
    
    Args:
        trial_expires_at: Trial expiration timestamp
        now: Current timestamp
        
    Returns:
        {
            "hours_until_expiry": float,
            "hours_since_activation": float
        }
    """
    if not trial_expires_at:
        return {
            "hours_until_expiry": 0.0,
            "hours_since_activation": 0.0
        }
    
    time_until_expiry = trial_expires_at - now
    hours_until_expiry = time_until_expiry.total_seconds() / 3600
    
    # Calculate hours since activation (trial is 72 hours)
    # hours_since_activation = 72 - hours_until_expiry
    hours_since_activation = 72 - hours_until_expiry
    
    return {
        "hours_until_expiry": max(0.0, hours_until_expiry),
        "hours_since_activation": max(0.0, hours_since_activation)
    }


async def should_send_notification(
    telegram_id: int,
    trial_expires_at: datetime,
    subscription_expires_at: datetime,
    notification_schedule: Dict[str, Any],
    notification_flags: Dict[str, bool],
    now: datetime,
    conn
) -> Tuple[bool, Optional[str]]:
    """
    Determine if a trial notification should be sent.
    
    Checks:
    - Subscription is still active
    - User doesn't have active paid subscription
    - Notification timing matches schedule
    - Notification hasn't been sent yet (idempotency)
    
    Args:
        telegram_id: Telegram ID of the user
        trial_expires_at: Trial expiration timestamp
        subscription_expires_at: Subscription expiration timestamp
        notification_schedule: Notification schedule entry with "hours", "key", "has_button", "db_flag"
        notification_flags: Dictionary of notification flags (e.g., {"trial_notif_6h_sent": False})
        now: Current timestamp
        conn: Database connection
        
    Returns:
        Tuple[bool, Optional[str]]:
        - (True, None) if notification should be sent
        - (False, reason) if notification should not be sent
    """
    # Check if subscription is still active
    if subscription_expires_at <= now:
        return (False, "subscription_expired")
    
    # Check if user has active paid subscription
    paid_subscription = await conn.fetchrow("""
        SELECT 1 FROM subscriptions 
        WHERE telegram_id = $1 
        AND source = 'payment'
        AND status = 'active'
        AND expires_at > $2
        LIMIT 1
    """, telegram_id, database._to_db_utc(now))
    
    if paid_subscription:
        return (False, "has_active_paid_subscription")
    
    # Calculate timing
    timing = calculate_trial_timing(trial_expires_at, now)
    hours_until_expiry = timing["hours_until_expiry"]
    hours_since_activation = timing["hours_since_activation"]
    
    # Check notification schedule
    hours = notification_schedule["hours"]
    db_flag = notification_schedule.get("db_flag", f"trial_notif_{hours}h_sent")
    
    # Check if already sent
    already_sent = notification_flags.get(db_flag, False)
    if already_sent:
        return (False, "already_sent")
    
    # Check timing window (within 1 hour after scheduled time)
    if hours_since_activation < hours:
        return (False, "too_early")
    
    if hours_since_activation >= hours + 1:
        return (False, "too_late")
    
    # Don't send if too close to expiry (final reminder handles that)
    if hours_until_expiry <= 6:
        return (False, "too_close_to_expiry")
    
    return (True, None)


async def should_send_final_reminder(
    telegram_id: int,
    trial_expires_at: datetime,
    subscription_expires_at: datetime,
    final_reminder_sent: bool,
    now: datetime,
    conn
) -> Tuple[bool, Optional[str]]:
    """
    Determine if final reminder (6h before expiry) should be sent.
    
    Args:
        telegram_id: Telegram ID of the user
        trial_expires_at: Trial expiration timestamp
        subscription_expires_at: Subscription expiration timestamp
        final_reminder_sent: Whether final reminder was already sent
        now: Current timestamp
        conn: Database connection
        
    Returns:
        Tuple[bool, Optional[str]]:
        - (True, None) if final reminder should be sent
        - (False, reason) if final reminder should not be sent
    """
    # Check if already sent
    if final_reminder_sent:
        return (False, "already_sent")
    
    # Check if subscription is still active
    if subscription_expires_at <= now:
        return (False, "subscription_expired")
    
    # Check if user has active paid subscription
    paid_subscription = await conn.fetchrow("""
        SELECT 1 FROM subscriptions 
        WHERE telegram_id = $1 
        AND source = 'payment'
        AND status = 'active'
        AND expires_at > $2
        LIMIT 1
    """, telegram_id, database._to_db_utc(now))
    
    if paid_subscription:
        return (False, "has_active_paid_subscription")
    
    # Calculate timing
    timing = calculate_trial_timing(trial_expires_at, now)
    hours_until_expiry = timing["hours_until_expiry"]
    
    # Final reminder — «последний час»: window (0.5, 1] час до истечения.
    # Worker тикает каждые 5 минут → в 30-минутном окне гарантированно
    # словит слот. Нижняя граница 0.5 нужна, чтобы `expire_trial_subscriptions`
    # на том же тике не проглотил уведомление, если триал уже помечен как
    # истёкший.
    if hours_until_expiry > 1:
        return (False, "too_early")

    if hours_until_expiry <= 0.5:
        return (False, "too_late")
    
    return (True, None)


# ====================================================================================
# Trial Completion Logic
# ====================================================================================

async def mark_trial_completed(
    telegram_id: int,
    conn
) -> bool:
    """
    Mark trial as completed (idempotent).
    
    Updates trial_completed_sent flag only if it was False.
    This ensures idempotency - multiple calls won't cause duplicate notifications.
    
    Args:
        telegram_id: Telegram ID of the user
        conn: Database connection
        
    Returns:
        True if flag was updated (notification should be sent),
        False if flag was already set (notification already sent)
    """
    result = await conn.execute("""
        UPDATE users 
        SET trial_completed_sent = TRUE 
        WHERE telegram_id = $1 
        AND trial_completed_sent = FALSE
    """, telegram_id)
    
    # asyncpg execute returns string like "UPDATE 1" or "UPDATE 0"
    return "1" in result


async def should_send_completion_notification(
    telegram_id: int,
    conn
) -> Tuple[bool, Optional[str]]:
    """
    Determine if trial completion notification should be sent.
    
    Checks:
    - User has used trial (trial_used_at IS NOT NULL)
    - Trial completion notification hasn't been sent yet
    
    Args:
        telegram_id: Telegram ID of the user
        conn: Database connection
        
    Returns:
        Tuple[bool, Optional[str]]:
        - (True, None) if notification should be sent
        - (False, reason) if notification should not be sent
    """
    user = await conn.fetchrow("""
        SELECT trial_used_at, trial_completed_sent
        FROM users
        WHERE telegram_id = $1
    """, telegram_id)
    
    if not user:
        return (False, "user_not_found")
    
    if not user["trial_used_at"]:
        return (False, "trial_not_used")
    
    if user["trial_completed_sent"]:
        return (False, "already_sent")
    
    return (True, None)


# ====================================================================================
# Notification Payload Preparation
# ====================================================================================

def prepare_notification_payload(
    notification_key: str,
    has_button: bool = False
) -> Dict[str, Any]:
    """
    Prepare notification payload for sending.
    
    This is a pure function that prepares the data structure for notification sending.
    Actual Telegram sending is done by the caller.
    
    Args:
        notification_key: Localization key for notification text
        has_button: Whether notification should include a button
        
    Returns:
        {
            "notification_key": str,
            "has_button": bool,
            "button_callback": Optional[str]  # "menu_buy_vpn" if has_button
        }
    """
    return {
        "notification_key": notification_key,
        "has_button": has_button,
        "button_callback": "menu_buy_vpn" if has_button else None
    }


def get_notification_schedule() -> List[Dict[str, Any]]:
    """
    Get trial notification schedule.
    
    Returns:
        List of notification schedule entries with:
        - hours: Hours since activation
        - key: Localization key
        - has_button: Whether to show button
        - db_flag: Database flag name (optional)
    """
    # На +48ч раньше висело дублирующее уведомление notification_60h с
    # текстом «12 часов пробного доступа» — оно летело в тот же слот,
    # что и inline-блок reminder_24h («заканчивается завтра») из
    # trial_notifications.py:184-195. Юзер получал два сообщения об
    # одном и том же в течение 5 минут. Убрано.
    #
    # 2026-XX: +6ч «✨ Просто напоминание, VPN лучше включать всегда»
    # тоже убрано — бледный push без CTA, юзер видит его как спам.
    # Осталось: T+5м bypass_activated, T+48ч reminder_24h,
    # T+69ч reminder_3h, T+71ч notification_71h.
    return []


def get_final_reminder_config() -> Dict[str, Any]:
    """
    Get final reminder configuration — «последний час».

    Раньше стояло `hours_before_expiry=6` — уведомление летело за
    6 часов до истечения, но текст `trial.notification_71h` говорил
    «🚨 Последний час пробного доступа». Юзеров это путало.
    Тайминг сдвинут на 1 час до истечения (реальный «последний час»);
    should_send_final_reminder тоже обновлён, чтоб window ловил слот.
    """
    return {
        "hours_before_expiry": 1,
        "notification_key": "trial.notification_71h",
        "has_button": True,
        "db_flag": "trial_notif_71h_sent"
    }


# ====================================================================================
# Trial Activation (T15 — docs/audit/02_payment_core_plan.md §A flow 9)
# ====================================================================================
#
# Unlike the helpers above, activation does I/O (grant_access, outbox job) and
# logs. It never talks to Telegram itself: the bot is only handed to the
# provisioning core / admin alerts.
#
# Flag "trial" off → the pre-T15 callback_activate_trial steps, moved as is.
# Flag "trial" on → ONE transaction, zero HTTP inside it:
#   pg_advisory_xact_lock("trial:{tg}") + users row FOR UPDATE
#   re-check availability under the lock (double click / concurrent calls)
#   UPDATE users SET trial_used_at … WHERE trial_used_at IS NULL   (mark used)
#   grant_access(conn, defer_panel=True, _caller_holds_transaction=True, source="trial", 3 days)
#   provisioning.enqueue("trial:{tg}", tariffs.for_trial(), premium_until=subscription_end)
# commit → provisioning.run_now (never raises; failure = job pending + admin alert).

ENTRYPOINT = "trial"
JOB_SOURCE = "trial"
TRIAL_DURATION = timedelta(days=3)

_TRIAL_STATE_SQL = """
    SELECT u.trial_used_at,
           s.status, s.expires_at, s.source,
           COALESCE(s.is_bypass_only, FALSE) AS is_bypass_only
    FROM users u
    LEFT JOIN subscriptions s ON s.telegram_id = u.telegram_id
    WHERE u.telegram_id = $1
    FOR UPDATE OF u
"""

_MARK_TRIAL_USED_SQL = """
    UPDATE users
    SET trial_used_at = CURRENT_TIMESTAMP,
        trial_expires_at = $1
    WHERE telegram_id = $2 AND trial_used_at IS NULL
"""


@dataclass(frozen=True)
class TrialGrant:
    """Outcome of a trial grant (callback_activate_trial builds its texts from it)."""
    subscription_end: datetime        # premium end written by grant_access
    trial_expires_at: datetime        # users.trial_expires_at (activation + 3 days)
    activated_at: datetime            # "now" of the activation
    uuid: Optional[str] = None        # None on the outbox path while activation is pending
    vpn_key: Optional[str] = None
    job_id: Optional[int] = None      # provisioning job id (outbox path only)
    applied: Optional[bool] = None    # run_now finished the panel work now (outbox path only)


def outbox_on() -> bool:
    """True when trial activation must go through the provisioning outbox."""
    return provisioning_flags.is_on(ENTRYPOINT)


async def grant_trial(telegram_id: int, *, bot=None) -> Optional[TrialGrant]:
    """Grant the trial to a user the caller already found eligible
    (callback_activate_trial checks database.is_eligible_for_trial first).

    Flag off: exactly the pre-T15 handler steps — grant_access(source="trial")
    on its own connection, then mark_trial_used; raises if no VPN key came back
    (the trial then stays unused). Never returns None.
    Flag on: the outbox transaction; returns None when trial_used_at is already
    set (a concurrent click won). Raises only if nothing was committed.
    """
    if outbox_on():
        return await _grant_trial_outbox(telegram_id, bot=bot, require_available=False)
    return await _grant_trial_legacy(telegram_id)


async def activate_trial(telegram_id: int, *, bot=None) -> bool:
    """Activate the 3-day trial (3 days premium + TRIAL_BYPASS_MB bypass) now.

    True if THIS call activated a trial; False if it is not available or a
    concurrent call activated it. Raises only if nothing was committed.

    Flag on: availability = trial_used_at IS NULL, no paid subscription
    (source='payment') and no active subscription — checked under the users-row
    lock inside the grant transaction. A bypass-only row (written by
    ensure_bypass_only_subscription right before, in the traffic-pack
    confirmation) is not an active subscription: those buyers are exactly the
    ones the trial is promised to.
    Flag off: the pre-T15 handler steps (is_eligible_for_trial → grant).
    """
    if outbox_on():
        return (await _grant_trial_outbox(telegram_id, bot=bot, require_available=True)) is not None
    if not await database.is_eligible_for_trial(telegram_id):
        return False
    await _grant_trial_legacy(telegram_id)
    return True


async def activate_trial_safely(telegram_id: int, *, bot=None, where: str) -> bool:
    """activate_trial for post-purchase hooks: never raises. The purchase is
    already committed; a failure is logged and alerted to the admin."""
    try:
        activated = await activate_trial(telegram_id, bot=bot)
    except Exception as e:
        logger.error(
            "TRIAL_ACTIVATION_FAILED: where=%s tg=%s %s: %s",
            where, telegram_id, type(e).__name__, e,
        )
        await _alert_trial_failure(telegram_id, where, e, bot=bot)
        return False
    logger.info("TRIAL_ACTIVATION_AFTER_PURCHASE: where=%s tg=%s activated=%s", where, telegram_id, activated)
    return activated


async def _alert_trial_failure(telegram_id: int, where: str, err: BaseException, *, bot) -> None:
    try:
        from app.services import admin_alerts
        target = bot
        if target is None:
            from app.api import payment_webhook
            target = getattr(payment_webhook, "_bot", None)
        if target is None:
            logger.warning("TRIAL_ACTIVATION_ALERT_NO_BOT: where=%s tg=%s", where, telegram_id)
            return
        await admin_alerts.send_alert(
            target, "payment",
            (
                "Trial activation after purchase FAILED\n"
                f"user: tg:{telegram_id}\n"
                f"where: {where}\n"
                f"error: {type(err).__name__}: {str(err)[:300]}\n"
                "The purchase is committed; the trial was NOT granted (nothing written)."
            ),
            force=True,
        )
    except Exception as e:
        logger.warning("TRIAL_ACTIVATION_ALERT_FAILED: where=%s tg=%s %s", where, telegram_id, e)


async def _grant_trial_legacy(telegram_id: int) -> TrialGrant:
    """The pre-T15 callback_activate_trial grant, moved as is (same calls, same order)."""
    duration = TRIAL_DURATION
    now = datetime.now(timezone.utc)
    trial_expires_at = now + duration

    # Сначала выдаём VPN-доступ. Если VPN API зависнет или упадёт,
    # флаг trial_used_at НЕ будет установлен — юзер сможет повторить попытку
    # (вместо того, чтобы «потерять» триал из-за таймаута внешнего API).
    result = await database.grant_access(
        telegram_id=telegram_id,
        duration=duration,
        source="trial",
        admin_telegram_id=None
    )

    uuid = result.get("uuid")
    vpn_key = result.get("vless_url")
    subscription_end = result.get("subscription_end")

    if not uuid or not vpn_key:
        raise Exception("Failed to create VPN access for trial")

    # VPN успешно выдан — теперь помечаем trial как использованный.
    # Если этот шаг упадёт, юзер получит доступ, а флаг останется пустым
    # (в худшем случае сможет активировать повторно — мелкий приемлемый риск
    # по сравнению с потерей триала из-за обрыва VPN API).
    mark_ok = await database.mark_trial_used(telegram_id, trial_expires_at)
    if not mark_ok:
        logger.error(
            f"mark_trial_used FAILED after grant_access succeeded: user={telegram_id} — "
            f"subscription active but trial_used_at not set"
        )
    return TrialGrant(
        subscription_end=subscription_end, trial_expires_at=trial_expires_at,
        activated_at=now, uuid=uuid, vpn_key=vpn_key,
    )


def _unavailable_reason(row, now: datetime, *, strict: bool) -> Optional[str]:
    """Why the trial cannot be activated now (None = it can)."""
    if row is None:
        return "no_user"
    if row["trial_used_at"] is not None:
        return "trial_used"
    if not strict:
        return None
    if row["source"] == "payment":
        return "paid_subscription"
    expires = row["expires_at"]
    if (
        row["status"] == "active"
        and expires is not None
        and database._from_db_utc(expires) > now
        and not row["is_bypass_only"]
    ):
        return "active_subscription"
    return None


def _rows_affected(status) -> int:
    """asyncpg status "UPDATE n" → n."""
    try:
        return int(str(status).split()[-1])
    except (ValueError, IndexError):
        return 0


async def _grant_trial_outbox(telegram_id: int, *, bot, require_available: bool) -> Optional[TrialGrant]:
    from app.services import tariffs

    ent = tariffs.for_trial(TRIAL_DURATION.days)  # config error → raises before any write
    return await _grant_premium_days_outbox(
        telegram_id, bot=bot, ent=ent, tag="TRIAL",
        reason_fn=lambda row, now: _unavailable_reason(row, now, strict=require_available),
    )


async def _grant_premium_days_outbox(telegram_id: int, *, bot, ent, tag: str, reason_fn) -> Optional[TrialGrant]:
    """The trial outbox transaction for entitlement `ent` (the trial, or the
    bypass-purchase gift): lock + availability (`reason_fn`) + mark trial used +
    grant_access(defer_panel) + job "trial:{tg}", zero HTTP; then run_now."""
    from app.services import provisioning

    key = f"trial:{telegram_id}"
    pool = await database.get_pool()
    if pool is None:
        raise RuntimeError("trial activation: DB pool unavailable")
    now = datetime.now(timezone.utc)
    trial_expires_at = now + TRIAL_DURATION
    reason: Optional[str] = None
    result: Dict[str, Any] = {}
    job_id: Optional[int] = None
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", key)
            row = await conn.fetchrow(_TRIAL_STATE_SQL, telegram_id)
            reason = reason_fn(row, now)
            if reason is None:
                existing = await conn.fetchval(
                    "SELECT id FROM provisioning_jobs WHERE idempotency_key = $1", key,
                )
                if existing is not None:
                    reason = "job_exists"  # trial_used_at was reset by hand: never a 2nd trial job
            if reason is None:
                marked = await conn.execute(
                    _MARK_TRIAL_USED_SQL, database._to_db_utc(trial_expires_at), telegram_id,
                )
                if _rows_affected(marked) != 1:
                    reason = "trial_used"
            if reason is None:
                result = await database.grant_access(
                    telegram_id=telegram_id,
                    duration=TRIAL_DURATION,
                    source="trial",
                    admin_telegram_id=None,
                    conn=conn,
                    _caller_holds_transaction=True,
                    defer_panel=True,
                ) or {}
                subscription_end = result.get("subscription_end")
                if subscription_end is None:
                    raise RuntimeError("grant_access returned no subscription_end")
                if tag == "BYPASS_GIFT":
                    await conn.execute(_GIFT_NO_TRIAL_BYPASS_NOTICE_SQL, telegram_id)
                job_id = await provisioning.enqueue(
                    conn, key=key, telegram_id=telegram_id, ent=ent,
                    premium_until=subscription_end, source=JOB_SOURCE,
                    context={"trial_expires_at": trial_expires_at.isoformat()},
                )
    if reason is not None:
        logger.info("%s_NOT_ACTIVATED: tg=%s reason=%s", tag, telegram_id, reason)
        return None
    applied = await provisioning.run_now(job_id, bot=bot)
    logger.info(
        "%s_OUTBOX: tg=%s job=%s premium_until=%s bypass_mb=%s applied=%s",
        tag, telegram_id, job_id, result["subscription_end"].isoformat(),
        ent.bypass_bytes // (1024 * 1024), applied,
    )
    return TrialGrant(
        subscription_end=result["subscription_end"], trial_expires_at=trial_expires_at,
        activated_at=now, uuid=result.get("uuid"), vpn_key=result.get("vless_url"),
        job_id=job_id, applied=applied,
    )


# ====================================================================================
# Gift for a GB pack bought from «🌐 Только обход блокировок» (owner, 2026-09-14)
# ====================================================================================
#
# A user WITHOUT an active premium subscription who buys GB on that screen gets
# the purchased GB (the pack path, unchanged) AND the trial's 3 days of basic
# premium as a gift — regardless of the "trial" feature flag and WITHOUT the
# trial's TRIAL_BYPASS_MB (the GB are the ones bought). One-time per user in the
# trial's store: users.trial_used_at (+ the job key "trial:{tg}"). A user who
# already used the trial or the gift gets only the GB.
# The gift IS the trial for everything after it: source='trial' +
# users.trial_expires_at, so the trial expiry turns the row back into bypass-only
# (the bypass entity keeps working) and a later paid purchase extends it.
#
# Outbox (the pack went through the outbox, or the "trial" entry point is on):
#   the trial transaction with tariffs.for_bypass_purchase_gift() — 0 bytes, so
#   the job never touches the bypass entity and cannot race the pack job.
# Legacy (both off; the pack GB were delivered synchronously just before):
#   claim trial_used_at under the advisory lock (ONE gift even for two concurrent
#   pack payments), then grant_access(source="trial") with no transaction held.
#   A failure releases the claim.
# Any failure → forced admin alert + background retry (60 s / 5 min / 15 min);
# the user is told when a retry grants it; a final forced alert if it gives up.

GIFT_RETRY_DELAYS_S = (60, 300, 900)
MSK = timezone(timedelta(hours=3))

_GIFT_STATE_SQL = """
    SELECT u.trial_used_at,
           s.status, s.expires_at, s.source,
           COALESCE(s.is_bypass_only, FALSE) AS is_bypass_only
    FROM users u
    LEFT JOIN subscriptions s ON s.telegram_id = u.telegram_id
    WHERE u.telegram_id = $1
"""

# #6 (docs/notifications/matrix.md): the gift row is source='trial', so the
# trial worker's fallback sent «🛡 Обход подключён — 500 МБ в подарок» 5 min
# later — false: the gift has no trial MB, the GB are the ones bought. Mark it
# as already sent in the gift's own write.
_GIFT_NO_TRIAL_BYPASS_NOTICE_SQL = """
    UPDATE subscriptions SET trial_notif_bypass_activated_sent = TRUE
    WHERE telegram_id = $1
"""

_RELEASE_GIFT_CLAIM_SQL = """
    UPDATE users
    SET trial_used_at = NULL, trial_expires_at = NULL
    WHERE telegram_id = $1 AND trial_expires_at = $2
"""

_gift_retry_tasks: Dict[int, Any] = {}   # telegram_id → pending retry task (one per user)


def format_gift_until(dt: datetime) -> str:
    """'DD.MM HH:MM' in Moscow time (the i18n text adds «МСК» / «MSK»)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK).strftime("%d.%m %H:%M")


def _gift_unavailable_reason(row, now: datetime) -> Optional[str]:
    """Why the bypass-purchase gift cannot be granted now (None = it can):
    the trial / gift was already used, or a premium subscription is active.
    A bypass-only row (placeholder expires_at) is not a premium subscription."""
    if row is None:
        return "no_user"
    if row["trial_used_at"] is not None:
        return "trial_used"
    expires = row["expires_at"]
    if (
        row["status"] == "active"
        and expires is not None
        and database._from_db_utc(expires) > now
        and not row["is_bypass_only"]
    ):
        return "active_subscription"
    return None


async def is_bypass_gift_available(telegram_id: int) -> bool:
    """The «🌐 Только обход блокировок» screen promises the gift only when the
    grant would give it: the same rule, read-only (no lock)."""
    try:
        pool = await database.get_pool()
        if pool is None:
            return False
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_GIFT_STATE_SQL, telegram_id)
    except Exception as e:
        logger.warning("BYPASS_GIFT_AVAILABILITY_FAILED: tg=%s %s: %s", telegram_id, type(e).__name__, e)
        return False
    return _gift_unavailable_reason(row, datetime.now(timezone.utc)) is None


async def grant_bypass_purchase_gift(
    telegram_id: int, *, bot=None, where: str, via_outbox: bool,
) -> Optional[TrialGrant]:
    """Grant the 3-day premium gift after a committed bypass-only GB purchase.

    Returns the grant (premium end = .subscription_end) or None: not eligible,
    or failed. Never raises — a failure is alerted (forced) and retried in the
    background; the purchase itself stays as it is."""
    try:
        return await _attempt_gift(telegram_id, bot=bot, via_outbox=via_outbox)
    except Exception as e:
        logger.error(
            "BYPASS_GIFT_FAILED: where=%s tg=%s outbox=%s %s: %s",
            where, telegram_id, via_outbox, type(e).__name__, e,
        )
        await _send_gift_alert(
            bot, telegram_id, where,
            f"error: {type(e).__name__}: {str(e)[:300]}\n"
            "The GB purchase is committed; the gift is NOT granted yet. "
            "Retrying in 1 / 5 / 15 min — a final alert follows if it gives up.",
            title="Bypass-purchase gift (3 days premium) FAILED",
        )
        _schedule_gift_retry(telegram_id, bot=bot, where=where, via_outbox=via_outbox)
        return None


async def _attempt_gift(telegram_id: int, *, bot, via_outbox: bool) -> Optional[TrialGrant]:
    if via_outbox:
        from app.services import tariffs
        ent = tariffs.for_bypass_purchase_gift(TRIAL_DURATION.days)
        return await _grant_premium_days_outbox(
            telegram_id, bot=bot, ent=ent, tag="BYPASS_GIFT", reason_fn=_gift_unavailable_reason,
        )
    return await _grant_gift_legacy(telegram_id)


async def _grant_gift_legacy(telegram_id: int) -> Optional[TrialGrant]:
    now = datetime.now(timezone.utc)
    trial_expires_at = now + TRIAL_DURATION
    key = f"trial:{telegram_id}"
    pool = await database.get_pool()
    if pool is None:
        raise RuntimeError("bypass gift: DB pool unavailable")
    reason: Optional[str] = None
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", key)
            row = await conn.fetchrow(_TRIAL_STATE_SQL, telegram_id)
            reason = _gift_unavailable_reason(row, now)
            if reason is None:
                existing = await conn.fetchval(
                    "SELECT id FROM provisioning_jobs WHERE idempotency_key = $1", key,
                )
                if existing is not None:
                    reason = "job_exists"
            if reason is None:
                marked = await conn.execute(
                    _MARK_TRIAL_USED_SQL, database._to_db_utc(trial_expires_at), telegram_id,
                )
                if _rows_affected(marked) != 1:
                    reason = "trial_used"
    if reason is not None:
        logger.info("BYPASS_GIFT_NOT_ACTIVATED: tg=%s reason=%s", telegram_id, reason)
        return None

    # The claim is committed; the panel work runs with NO transaction held.
    try:
        if not await database.get_remnawave_uuid(telegram_id):
            # The pack GB are not delivered (no bypass entity in the DB): the legacy
            # grant would create a trial-size (TRIAL_BYPASS_MB) bypass entity.
            raise RuntimeError("bypass entity missing (pack GB not delivered yet)")
        result = await database.grant_access(
            telegram_id=telegram_id,
            duration=TRIAL_DURATION,
            source="trial",
            admin_telegram_id=None,
        ) or {}
        subscription_end = result.get("subscription_end")
        if subscription_end is None:
            raise RuntimeError("grant_access returned no subscription_end")
        try:
            async with pool.acquire() as conn:
                await conn.execute(_GIFT_NO_TRIAL_BYPASS_NOTICE_SQL, telegram_id)
        except Exception as e:  # noqa: BLE001 — the gift stands; worst case one wrong notice
            logger.warning("BYPASS_GIFT_NOTICE_FLAG_FAILED: tg=%s %s", telegram_id, type(e).__name__)
    except Exception:
        landed = await _gift_landed_until(telegram_id, now)
        if landed is not None:
            # DB granted, the panel sync failed: purchase_flow already alerted
            # and re-syncs the panel to the DB date — the gift stands.
            logger.error(
                "BYPASS_GIFT_PANEL_SYNC_FAILED: tg=%s premium_until=%s — DB granted, panel re-sync pending",
                telegram_id, landed.isoformat(),
            )
            return TrialGrant(subscription_end=landed, trial_expires_at=trial_expires_at, activated_at=now)
        await _release_gift_claim(pool, telegram_id, trial_expires_at)
        raise
    logger.info(
        "BYPASS_GIFT_LEGACY: tg=%s premium_until=%s action=%s",
        telegram_id, subscription_end.isoformat(), result.get("action"),
    )
    return TrialGrant(
        subscription_end=subscription_end, trial_expires_at=trial_expires_at,
        activated_at=now, uuid=result.get("uuid"), vpn_key=result.get("vless_url"),
    )


async def _gift_landed_until(telegram_id: int, now: datetime) -> Optional[datetime]:
    """Premium end if the gift's DB part is in place (active trial row), else None."""
    try:
        sub = await database.get_subscription_any(telegram_id)
    except Exception:
        return None
    if not sub or sub.get("source") != "trial" or sub.get("is_bypass_only") or sub.get("status") != "active":
        return None
    expires = sub.get("expires_at")
    if not isinstance(expires, datetime):
        return None
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires if expires > now else None


async def _release_gift_claim(pool, telegram_id: int, trial_expires_at: datetime) -> None:
    try:
        async with pool.acquire() as conn:
            await conn.execute(_RELEASE_GIFT_CLAIM_SQL, telegram_id, database._to_db_utc(trial_expires_at))
    except Exception as e:
        logger.critical(
            "BYPASS_GIFT_CLAIM_RELEASE_FAILED: tg=%s %s: %s — trial_used_at stays set, gift NOT granted",
            telegram_id, type(e).__name__, e,
        )


def _schedule_gift_retry(telegram_id: int, *, bot, where: str, via_outbox: bool) -> None:
    import asyncio
    tg = int(telegram_id)
    running = _gift_retry_tasks.get(tg)
    if running is not None and not running.done():
        return
    try:
        task = asyncio.get_running_loop().create_task(
            _gift_retry(tg, bot=bot, where=where, via_outbox=via_outbox),
        )
    except RuntimeError as e:
        logger.error("BYPASS_GIFT_RETRY_NOT_SCHEDULED: tg=%s %s", tg, e)
        return
    _gift_retry_tasks[tg] = task

    def _forget(done_task, _tg=tg):
        if _gift_retry_tasks.get(_tg) is done_task:
            _gift_retry_tasks.pop(_tg, None)
    task.add_done_callback(_forget)


async def _gift_retry_sleep(seconds: float) -> None:
    """Separate hook so tests can control the retry timing."""
    import asyncio
    await asyncio.sleep(seconds)


async def _gift_retry(telegram_id: int, *, bot, where: str, via_outbox: bool) -> None:
    last_err: Optional[BaseException] = None
    for delay in GIFT_RETRY_DELAYS_S:
        await _gift_retry_sleep(delay)
        try:
            grant = await _attempt_gift(telegram_id, bot=bot, via_outbox=via_outbox)
        except Exception as e:
            last_err = e
            logger.warning("BYPASS_GIFT_RETRY_FAILED: tg=%s %s: %s", telegram_id, type(e).__name__, e)
            continue
        if grant is None:
            logger.warning("BYPASS_GIFT_RETRY_NOT_ELIGIBLE: tg=%s", telegram_id)
            await _send_gift_alert(
                bot, telegram_id, where,
                "The retry found the user no longer eligible (trial/gift already used or an "
                "active subscription). Nothing was granted by the retry — check manually.",
                title="Bypass-purchase gift: retry stopped",
            )
            return
        logger.info(
            "BYPASS_GIFT_GRANTED_ON_RETRY: tg=%s premium_until=%s",
            telegram_id, grant.subscription_end.isoformat(),
        )
        await _notify_gift_granted_late(bot, telegram_id, grant)
        return
    await _send_gift_alert(
        bot, telegram_id, where,
        f"last error: {type(last_err).__name__}: {str(last_err)[:300]}\n"
        "All retries failed. Grant 3 days of premium manually (dashboard).",
        title="Bypass-purchase gift: GAVE UP",
    )


def _alert_bot(bot):
    if bot is not None:
        return bot
    try:
        from app.api import payment_webhook
        return getattr(payment_webhook, "_bot", None)
    except Exception:
        return None


async def _send_gift_alert(bot, telegram_id: int, where: str, body: str, *, title: str) -> None:
    try:
        from app.services import admin_alerts
        target = _alert_bot(bot)
        if target is None:
            logger.warning("BYPASS_GIFT_ALERT_NO_BOT: where=%s tg=%s", where, telegram_id)
            return
        await admin_alerts.send_alert(
            target, "payment",
            f"{title}\nuser: tg:{telegram_id}\nwhere: {where}\n{body}",
            force=True,
        )
    except Exception as e:
        logger.warning("BYPASS_GIFT_ALERT_FAILED: where=%s tg=%s %s", where, telegram_id, e)


async def _notify_gift_granted_late(bot, telegram_id: int, grant: TrialGrant) -> None:
    """A retry granted the gift after the purchase message was sent: tell the user."""
    try:
        from app.i18n import get_text
        from app.services.language_service import resolve_user_language
        from app.utils.telegram_safe import safe_send_message
        target = _alert_bot(bot)
        if target is None:
            return
        language = await resolve_user_language(telegram_id)
        text = get_text(language, "bypass.gift_premium_granted",
                        until=format_gift_until(grant.subscription_end))
        await safe_send_message(target, telegram_id, text, parse_mode="HTML")
    except Exception as e:
        logger.warning("BYPASS_GIFT_LATE_NOTICE_FAILED: tg=%s %s", telegram_id, e)
