"""
Notification Service Layer

This module provides business logic for notifications, reminders, and idempotency checks.
It handles all decisions about when to send notifications and when to skip them.

All functions are pure business logic:
- No aiogram imports
- No logging
- No Telegram calls
- Pure business logic only
"""

import logging
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from enum import Enum

import database
from app.services.notifications.exceptions import (
    NotificationServiceError,
    NotificationAlreadySentError,
    InvalidReminderTypeError,
    ReminderNotApplicableError,
)

logger = logging.getLogger(__name__)


# ====================================================================================
# Reminder Types
# ====================================================================================

class ReminderType(Enum):
    """Types of reminders"""
    REMINDER_7D = "reminder_7d"  # 7 days before expiry (paid subscriptions)
    REMINDER_3D = "reminder_3d"  # 3 days before expiry (paid subscriptions)
    REMINDER_1D = "reminder_1d"  # 1 day before expiry (paid subscriptions)
    REMINDER_24H = "reminder_24h"  # 24 hours before expiry
    REMINDER_3H = "reminder_3h"  # 3 hours before expiry (paid subscriptions) — special 15% offer
    REMINDER_6H = "reminder_6h"  # 6 hours before expiry (admin 1-day grants)
    ADMIN_1DAY_6H = "admin_1day_6h"  # 6 hours before expiry (admin 1-day grants)
    ADMIN_7DAYS_24H = "admin_7days_24h"  # 24 hours before expiry (admin 7-day grants)
    # Trial reminders
    TRIAL_24H = "trial_24h"  # 24 hours before trial expiry
    TRIAL_3H = "trial_3h"  # 3 hours before trial expiry — 15% discount


# ====================================================================================
# Result Types
# ====================================================================================

@dataclass
class ReminderDecision:
    """Decision about whether to send a reminder"""
    should_send: bool
    reminder_type: Optional[ReminderType]
    reason: Optional[str] = None  # Reason if should_send is False


# ====================================================================================
# Reminder Time Calculations
# ====================================================================================

def calculate_time_until_expiry(expires_at: datetime, now: Optional[datetime] = None) -> timedelta:
    """
    Calculate time until subscription expiry.
    
    Args:
        expires_at: Subscription expiration date
        now: Current time (defaults to datetime.now(timezone.utc))
        
    Returns:
        timedelta until expiry
    """
    if now is None:
        now = datetime.now(timezone.utc)
    
    return expires_at - now


def is_within_time_window(
    time_until_expiry: timedelta,
    target_duration: timedelta,
    tolerance: timedelta
) -> bool:
    """
    Check if time until expiry is within a time window.
    
    Args:
        time_until_expiry: Time until subscription expires
        target_duration: Target duration (e.g., timedelta(days=3))
        tolerance: Tolerance window (e.g., timedelta(hours=2))
        
    Returns:
        True if within window, False otherwise
    """
    lower_bound = target_duration - tolerance
    upper_bound = target_duration + tolerance
    
    return lower_bound <= time_until_expiry <= upper_bound


# ====================================================================================
# Reminder Windows (dashboard-configurable)
# ====================================================================================

# Ключ реестра автоуведомлений для каждого платного напоминания. Через них
# дашборд отдаёт trigger_config, и по ним же считается статистика отправок.
PAID_REMINDER_NOTIFICATION_KEYS: Dict[ReminderType, str] = {
    ReminderType.REMINDER_7D: "subscription.reminder_7d",
    ReminderType.REMINDER_3D: "subscription.reminder_3d",
    ReminderType.REMINDER_1D: "subscription.reminder_1d",
    ReminderType.REMINDER_3H: "subscription.reminder_3h",
}

# Окна отправки по умолчанию: (за сколько часов до конца, допуск в часах).
#
# Раньше эти числа были вбиты прямо в should_send_reminder (3 / 2.4 / 1 / 0.5 ч
# допуска) и расходились с default_trigger в реестре автоуведомлений
# (12 / 6 / 2 / 1 ч). Админ расширял допуск в дашборде, ничего не менялось, и
# он считал настройку рабочей. Теперь источник один — реестр, а дашборд
# реально управляет окном.
DEFAULT_PAID_REMINDER_WINDOWS: Dict[ReminderType, Tuple[float, float]] = {
    ReminderType.REMINDER_7D: (24 * 7, 12.0),
    ReminderType.REMINDER_3D: (24 * 3, 6.0),
    ReminderType.REMINDER_1D: (24.0, 2.0),
    ReminderType.REMINDER_3H: (3.0, 1.0),
}

# Окна для админских грантов. Из дашборда не настраиваются: ключей реестра
# для них нет, тексты берутся напрямую из i18n (см. reminders.py).
ADMIN_GRANT_REMINDER_WINDOWS: Dict[int, Tuple[float, float, ReminderType]] = {
    1: (6.0, 0.5, ReminderType.ADMIN_1DAY_6H),    # грант на сутки → за 6 часов
    7: (24.0, 1.0, ReminderType.ADMIN_7DAYS_24H),  # грант на неделю → за сутки
}


def _positive_float(raw: Any) -> Optional[float]:
    """Число из trigger_config или None, если это мусор.

    trigger_config — JSON из базы, который правит человек через дашборд.
    Строка, ноль или отрицательное значение здесь означали бы окно, в которое
    никто никогда не попадёт, поэтому такие значения игнорируем и работаем на
    дефолте, а не молча выключаем напоминание.
    """
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return value


def resolve_reminder_window(
    reminder_type: ReminderType,
    trigger_configs: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[timedelta, timedelta]:
    """Вернуть (за сколько до конца, допуск) для платного напоминания.

    `trigger_configs` — то, что дашборд хранит в automated_notifications:
    {registry_key: {"before_expiry_hours": …, "tolerance_hours": …}}.
    Отсутствующий ключ или битые значения → дефолт из
    DEFAULT_PAID_REMINDER_WINDOWS.
    """
    before_h, tolerance_h = DEFAULT_PAID_REMINDER_WINDOWS[reminder_type]
    key = PAID_REMINDER_NOTIFICATION_KEYS.get(reminder_type)
    cfg = (trigger_configs or {}).get(key or "") or {}
    before_h = _positive_float(cfg.get("before_expiry_hours")) or before_h
    tolerance_h = _positive_float(cfg.get("tolerance_hours")) or tolerance_h
    return timedelta(hours=before_h), timedelta(hours=tolerance_h)


def reminder_query_windows(
    trigger_configs: Optional[Dict[str, Dict[str, Any]]] = None,
) -> list:
    """Все окна воркера напоминаний как (часов до конца, допуск, имя флага).

    Нужны отбору кандидатов в SQL (database/reminders_queries.py). Считаем их
    здесь, чтобы выборка и решение шли по ОДНИМ И ТЕМ ЖЕ числам: разъедутся —
    воркер будет либо вычитывать людей, которых всё равно пропустит, либо
    (хуже) не вычитывать тех, кому пора писать.
    """
    out = []
    for rtype in DEFAULT_PAID_REMINDER_WINDOWS:
        target, tolerance = resolve_reminder_window(rtype, trigger_configs)
        out.append((
            target.total_seconds() / 3600.0,
            tolerance.total_seconds() / 3600.0,
            get_reminder_flag_name(rtype),
        ))
    for before_h, tolerance_h, rtype in ADMIN_GRANT_REMINDER_WINDOWS.values():
        out.append((before_h, tolerance_h, get_reminder_flag_name(rtype)))
    return out


# ====================================================================================
# Reminder Decision Logic
# ====================================================================================

def should_send_reminder(
    subscription: Dict[str, Any],
    now: Optional[datetime] = None,
    trigger_configs: Optional[Dict[str, Dict[str, Any]]] = None,
) -> ReminderDecision:
    """
    Determine if a reminder should be sent for a subscription.
    
    This function implements all business rules for reminders:
    - Different rules for admin grants vs paid subscriptions
    - Time window checks
    - Idempotency checks (already sent flags)
    
    Args:
        subscription: Subscription dictionary from database
        now: Current time (defaults to datetime.now(timezone.utc))
        trigger_configs: окна из дашборда, {registry_key: trigger_config}.
            Читает их вызывающий (один раз на проход воркера, а не на
            пользователя), функция остаётся чистой. None → дефолтные окна.

    Returns:
        ReminderDecision with should_send flag and reminder type
    """
    if now is None:
        now = datetime.now(timezone.utc)
    
    expires_at = subscription.get("expires_at")
    if not expires_at:
        return ReminderDecision(
            should_send=False,
            reminder_type=None,
            reason="Subscription has no expiration date"
        )
    
    # Parse expires_at if it's a string
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
        except Exception as e:
            logger.debug("Invalid expiration date format: %s", e)
            return ReminderDecision(
                should_send=False,
                reminder_type=None,
                reason="Invalid expiration date format"
            )
    
    # Check if subscription has expired
    if expires_at <= now:
        return ReminderDecision(
            should_send=False,
            reminder_type=None,
            reason="Subscription has already expired"
        )
    
    time_until_expiry = calculate_time_until_expiry(expires_at, now)

    # Skip trial subscriptions — они обслуживаются отдельным worker'ом
    # (trial_notifications.py), который ориентируется на
    # users.trial_expires_at, а не на subscriptions.expires_at.
    #
    # ВАЖНО: триальная subscription_row обычно создаётся с
    #   subscription_type='basic' (или 'plus'), source='trial'.
    # Поэтому фильтровать только по subscription_type недостаточно —
    # юзеры с триалом пройдут фильтр и получат paid-reminder
    # параллельно с trial-reminder'ом (видели в логах двойные
    # уведомления «Пробный период заканчивается завтра» + «Подписка
    # заканчивается завтра» с разницей в 4 минуты). Дополнительно
    # ловим триал по source.
    subscription_type = (subscription.get("subscription_type") or "").strip().lower()
    source = (subscription.get("source") or "").strip().lower()
    if subscription_type == "trial" or source == "trial":
        return ReminderDecision(
            should_send=False,
            reminder_type=None,
            reason="Trial subscription — handled by trial_notifications worker"
        )

    # Determine subscription type
    admin_grant_days = subscription.get("admin_grant_days")
    last_action_type = subscription.get("last_action_type")
    is_admin_grant = admin_grant_days is not None or last_action_type == "admin_grant"
    
    # ADMIN-GRANTED ACCESS
    if is_admin_grant:
        admin_window = ADMIN_GRANT_REMINDER_WINDOWS.get(admin_grant_days)
        if admin_window:
            before_h, tolerance_h, admin_type = admin_window
            if is_within_time_window(
                time_until_expiry, timedelta(hours=before_h), timedelta(hours=tolerance_h)
            ):
                flag = get_reminder_flag_name(admin_type)
                if subscription.get(flag, False):
                    return ReminderDecision(
                        should_send=False,
                        reminder_type=admin_type,
                        reason=f"Reminder already sent ({flag} flag)"
                    )
                return ReminderDecision(should_send=True, reminder_type=admin_type)


    # PAID SUBSCRIPTIONS
    else:
        # Окна берём из trigger_config (дашборд), см. resolve_reminder_window.
        # Порядок проверки — от дальнего к ближнему: если админ раздует допуски
        # так, что окна перекроются, выигрывает более раннее напоминание.
        _flags = {
            ReminderType.REMINDER_7D: "reminder_7d_sent",
            ReminderType.REMINDER_3D: "reminder_3d_sent",
            ReminderType.REMINDER_1D: "reminder_1d_sent",
            ReminderType.REMINDER_3H: "reminder_3h_sent",
        }
        for _rtype in (
            ReminderType.REMINDER_7D,
            ReminderType.REMINDER_3D,
            ReminderType.REMINDER_1D,
            ReminderType.REMINDER_3H,
        ):
            target, tolerance = resolve_reminder_window(_rtype, trigger_configs)
            if not is_within_time_window(time_until_expiry, target, tolerance):
                continue
            if subscription.get(_flags[_rtype], False):
                return ReminderDecision(
                    should_send=False,
                    reminder_type=_rtype,
                    reason=f"Reminder already sent ({_flags[_rtype]} flag)"
                )
            return ReminderDecision(should_send=True, reminder_type=_rtype)


    # No reminder should be sent
    return ReminderDecision(
        should_send=False,
        reminder_type=None,
        reason="Not within any reminder time window"
    )


def get_reminder_flag_name(reminder_type: ReminderType) -> str:
    """
    Get database flag name for a reminder type.
    
    Args:
        reminder_type: Type of reminder
        
    Returns:
        Database flag name (e.g., "reminder_3d_sent")
    """
    mapping = {
        ReminderType.REMINDER_7D: "reminder_7d_sent",
        ReminderType.REMINDER_3D: "reminder_3d_sent",
        ReminderType.REMINDER_1D: "reminder_1d_sent",
        ReminderType.REMINDER_24H: "reminder_24h_sent",
        ReminderType.REMINDER_3H: "reminder_3h_sent",
        ReminderType.REMINDER_6H: "reminder_6h_sent",
        ReminderType.ADMIN_1DAY_6H: "reminder_6h_sent",
        ReminderType.ADMIN_7DAYS_24H: "reminder_24h_sent",
        ReminderType.TRIAL_24H: "trial_notif_24h_sent",
        ReminderType.TRIAL_3H: "trial_notif_3h_sent",
    }
    
    return mapping.get(reminder_type, "reminder_sent")


# ====================================================================================
# Payment Notification Idempotency
# ====================================================================================

async def check_notification_idempotency(
    payment_id: int,
    conn: Optional[Any] = None
) -> bool:
    """
    Check if payment notification has already been sent (idempotency check).
    
    Args:
        payment_id: Payment ID
        conn: Database connection (if None, creates new connection)
        
    Returns:
        True if notification already sent, False otherwise
    """
    return await database.is_payment_notification_sent(payment_id, conn=conn)


async def mark_notification_sent(
    payment_id: int,
    conn: Optional[Any] = None
) -> bool:
    """
    Mark payment notification as sent (idempotency).
    
    Args:
        payment_id: Payment ID
        conn: Database connection (if None, creates new connection)
        
    Returns:
        True if marked successfully, False if already marked
    """
    return await database.mark_payment_notification_sent(payment_id, conn=conn)


async def mark_reminder_sent(
    telegram_id: int,
    reminder_type: ReminderType,
    conn: Optional[Any] = None
) -> None:
    """
    Mark reminder as sent for a user.
    
    Args:
        telegram_id: Telegram ID of the user
        reminder_type: Type of reminder that was sent
        conn: Database connection (if None, creates new connection)
    """
    flag_name = get_reminder_flag_name(reminder_type)
    
    if conn is None:
        await database.mark_reminder_flag_sent(telegram_id, flag_name)
    else:
        # Use pre-built query (no f-string SQL interpolation)
        query = database._REMINDER_FLAG_UPDATE_QUERIES.get(flag_name)
        if query is None:
            raise ValueError(
                f"Invalid flag_name '{flag_name}'. "
                f"Allowed: {sorted(database._ALLOWED_REMINDER_FLAGS)}"
            )
        await conn.execute(query, telegram_id)


# ====================================================================================
# Referral Notification Logic
# ====================================================================================

def format_referral_notification_text(
    purchase_amount: float,
    cashback_amount: float,
    cashback_percent: int,
    paid_referrals_count: int,
    referrals_needed: int,
    action_type: str,
    subscription_period: Optional[str] = None,
    language: str = "en",
    # Legacy parameters — ignored, kept for backward compatibility
    referred_username: Optional[str] = None,
    referred_id: int = 0,
) -> str:
    """
    Format referral cashback notification text.
    """
    from app.i18n import get_text as i18n_get_text

    # Русские пользователи — новый стиль «Круга Амбассадоров» (рандом из 3 шаблонов).
    if language == "ru":
        from app.services.notifications.loyalty_pushes import pick_purchase_push
        # Определяем следующий тир по количеству оплативших.
        if paid_referrals_count < 25:
            next_tier = "Хранитель"
        elif paid_referrals_count < 50:
            next_tier = "Инсайдер"
        elif paid_referrals_count < 75:
            next_tier = "Лидер"
        elif paid_referrals_count < 100:
            next_tier = "Амбассадор"
        else:
            next_tier = None
        return pick_purchase_push(
            amount=cashback_amount,
            percent=cashback_percent,
            next_level_name=next_tier,
            referrals_needed=referrals_needed,
        )

    if referrals_needed > 0:
        if language == "ru":
            if referrals_needed % 10 == 1 and referrals_needed % 100 != 11:
                friend_word = i18n_get_text(language, "referral.friend_singular")
            elif 2 <= referrals_needed % 10 <= 4 and (referrals_needed % 100 < 10 or referrals_needed % 100 >= 20):
                friend_word = i18n_get_text(language, "referral.friend_dual")
            else:
                friend_word = i18n_get_text(language, "referral.friend_plural")
        else:
            friend_word = i18n_get_text(language, "referral.friend_plural")

        progress_text = i18n_get_text(
            language,
            "referral.cashback_progress",
            needed=referrals_needed,
            friend=friend_word
        )
    else:
        progress_text = i18n_get_text(language, "referral.cashback_max_level")

    # action_type подставляется в заголовок и в строку суммы у части языков
    # (de, ar, kk, tj, uz): «Ваш реферал совершил {покупку}», «Сумма
    # {покупки}». В ru и en шаблоны его не используют, но передавать
    # безопасно — лишние аргументы формат игнорирует.
    #
    # Раньше он не передавался вовсе, и у этих пяти языков обе строки падали
    # с KeyError('action_type'): человек видел сырой шаблон с фигурными
    # скобками вместо суммы и заголовка. Само слово тоже локализуется —
    # referral.action_purchase / _renewal / _topup, они есть у всех языков.
    _action_key = {
        "purchase": "referral.action_purchase",
        "renewal": "referral.action_renewal",
        "topup": "referral.action_topup",
    }.get((action_type or "purchase").strip().lower(), "referral.action_purchase")
    action_word = i18n_get_text(language, _action_key)

    title = i18n_get_text(
        language, "referral.cashback_title", action_type=action_word,
    )
    amount_line = i18n_get_text(
        language,
        "referral.cashback_amount",
        amount=purchase_amount,
        action_type=action_word,
    )

    notification_text = f"{title}\n\n{amount_line}\n"

    if subscription_period:
        period_line = i18n_get_text(
            language,
            "referral.cashback_subscription_period",
            period=subscription_period
        )
        notification_text += f"{period_line}\n"

    reward_line = i18n_get_text(
        language,
        "referral.cashback_reward",
        amount=cashback_amount,
        percent=cashback_percent
    )

    level_line = i18n_get_text(language, "referral.cashback_level", percent=cashback_percent)
    balance_line = i18n_get_text(language, "referral.cashback_balance_auto")

    notification_text += (
        f"{reward_line}\n\n"
        f"{level_line}\n"
        f"{progress_text}\n\n"
        f"{balance_line}"
    )

    return notification_text
