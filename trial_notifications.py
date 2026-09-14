"""Модуль для отправки уведомлений о пробном периоде (trial)
Отдельный от reminders.py для платных подписок
"""
import asyncio
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Tuple
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from app.utils.telegram_safe import safe_send_message
import asyncpg
import database
import config
from app import i18n
from app.services.trials import service as trial_service
from app.services.language_service import resolve_user_language
from app.utils.logging_helpers import (
    log_worker_iteration_start,
    log_worker_iteration_end,
    classify_error,
)

logger = logging.getLogger(__name__)

# Фото для уведомления «3 часа до отключения» (trial.reminder_3h).
# Stage/prod различаются — file_id привязан к боту.
_REMINDER_3H_PHOTO = {
    "prod": "AgACAgQAAxkBAAF_FwdqeJDS1H8mmP976s4J_8Zvmo9xZQACrA9rG9xjyVOAZefu4qWD6gEAAwIAA3kAAz0E",
    "stage": "",
}


async def _safe_send_photo_or_text(
    bot: Bot,
    telegram_id: int,
    photo_id: str,
    text: str,
    reply_markup: InlineKeyboardMarkup = None,
):
    """Отправить фото с caption + fallback на text-only.

    Если photo_id пустой или Telegram отказал (устаревший file_id и т.п.) —
    fallback на safe_send_message (обычный текст). Возвращает Message или None
    (при graceful-failure — совместимо с safe_send_message контрактом)."""
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
    from app.utils.telegram_safe import convert_tg_emoji

    if not photo_id:
        return await safe_send_message(bot, telegram_id, text, reply_markup=reply_markup)

    caption = convert_tg_emoji(text)
    try:
        return await bot.send_photo(
            chat_id=telegram_id, photo=photo_id, caption=caption,
            parse_mode="HTML", reply_markup=reply_markup,
        )
    except TelegramForbiddenError:
        logger.warning(f"SAFE_SEND_PHOTO_FORBIDDEN user={telegram_id}")
        try:
            await database.mark_user_unreachable(telegram_id)
        except Exception:
            pass
        return None
    except TelegramBadRequest as e:
        err = str(e).lower()
        if "chat not found" in err:
            logger.warning(f"SAFE_SEND_PHOTO_CHAT_NOT_FOUND user={telegram_id}")
            try:
                await database.mark_user_unreachable(telegram_id)
            except Exception:
                pass
            return None
        # Устаревший file_id или другая photo-специфичная ошибка → text fallback.
        logger.warning(
            "SAFE_SEND_PHOTO_FAIL_FALLBACK_TEXT user=%s err=%s", telegram_id, e,
        )
        return await safe_send_message(bot, telegram_id, text, reply_markup=reply_markup)
    except Exception:
        logger.exception(f"SAFE_SEND_PHOTO_UNKNOWN user={telegram_id}")
        return None


# Singleton guard: предотвращает повторный запуск scheduler
_TRIAL_SCHEDULER_STARTED = False
_TRIAL_SCHEDULER_LOCK = asyncio.Lock()

# SECURITY: Pre-built SQL queries for each trial flag.
# Eliminates f-string SQL interpolation entirely — only static SQL strings are used.
_TRIAL_FLAG_UPDATE_QUERIES = {
    "trial_notif_6h_sent": (
        "UPDATE subscriptions SET trial_notif_6h_sent = TRUE "
        "WHERE telegram_id = $1 AND source = 'trial' AND status = 'active'"
    ),
    "trial_notif_60h_sent": (
        "UPDATE subscriptions SET trial_notif_60h_sent = TRUE "
        "WHERE telegram_id = $1 AND source = 'trial' AND status = 'active'"
    ),
    "trial_notif_71h_sent": (
        "UPDATE subscriptions SET trial_notif_71h_sent = TRUE "
        "WHERE telegram_id = $1 AND source = 'trial' AND status = 'active'"
    ),
    # New trial notification flags (24h and 3h before expiry)
    "trial_notif_24h_sent": (
        "UPDATE subscriptions SET trial_notif_24h_sent = TRUE "
        "WHERE telegram_id = $1 AND source = 'trial' AND status = 'active'"
    ),
    "trial_notif_3h_sent": (
        "UPDATE subscriptions SET trial_notif_3h_sent = TRUE "
        "WHERE telegram_id = $1 AND source = 'trial' AND status = 'active'"
    ),
    # Уведомление «🛡 Обход белых списков подключён» через ~5 минут
    # после активации триала. См. migration 062.
    "trial_notif_bypass_activated_sent": (
        "UPDATE subscriptions SET trial_notif_bypass_activated_sent = TRUE "
        "WHERE telegram_id = $1 AND source = 'trial' AND status = 'active'"
    ),
}


def _get_trial_flag_query(flag_name: str) -> str:
    """Get pre-built SQL query for trial flag. Raises ValueError if invalid."""
    query = _TRIAL_FLAG_UPDATE_QUERIES.get(flag_name)
    if query is None:
        raise ValueError(
            f"Invalid trial flag_name '{flag_name}'. "
            f"Allowed: {sorted(_TRIAL_FLAG_UPDATE_QUERIES)}"
        )
    return query

# Расписание уведомлений получается из service layer
TRIAL_NOTIFICATION_SCHEDULE = trial_service.get_notification_schedule()

# STEP 3 — PART B: WORKER LOOP SAFETY
# Minimum safe sleep on failure to prevent tight retry storms
MINIMUM_SAFE_SLEEP_ON_FAILURE = 60  # seconds (1 minute, less than normal 5-minute interval)

# Production-safe batching: prevent unbounded fetch, long-held connections, event loop starvation
BATCH_SIZE = 100
BATCH_YIELD_SLEEP = 0  # asyncio.sleep(0) for cooperative yield


def get_trial_buy_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура для покупки доступа (в уведомлениях trial)"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n.get_text(language, "main.buy"),
            callback_data="menu_buy_vpn"
        )]
    ])
    return keyboard


def get_trial_discount_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой скидки 15% для триала (за 3ч до окончания)"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n.get_text(language, "trial.reminder_3h_discount_btn"),
            callback_data="trial_discount_15"
        )],
        [InlineKeyboardButton(
            text=i18n.get_text(language, "main.buy"),
            callback_data="menu_buy_vpn"
        )]
    ])
    return keyboard


async def send_trial_notification(
    bot: Bot,
    pool,
    telegram_id: int,
    notification_key: str,
    has_button: bool = False,
    custom_text: str | None = None,
) -> Tuple[bool, str]:
    """Отправить уведомление о trial

    Args:
        bot: Bot instance
        pool: Database connection pool
        telegram_id: Telegram ID пользователя
        notification_key: Ключ локализации для текста уведомления
        has_button: Показывать ли кнопку "Купить доступ"
        custom_text: Готовый override-текст (из automated_notifications).
                     Если None — используем i18n по notification_key.

    Returns:
        Tuple[bool, str] - статус отправки:
        - (True, "sent") - уведомление отправлено успешно
        - (False, "failed_permanently") - постоянная ошибка (Forbidden/blocked), больше не пытаться
        - (False, "failed_temporary") - временная ошибка, можно повторить позже
    """
    try:
        language = await resolve_user_language(telegram_id)

        # Готовый override админа или i18n-дефолт.
        text = custom_text or i18n.get_text(language, notification_key)

        # Формируем клавиатуру (если нужно)
        reply_markup = None
        if has_button:
            reply_markup = get_trial_buy_keyboard(language)

        # Отправляем уведомление (safe_send_message handles chat_not_found, blocked)
        sent = await safe_send_message(bot, telegram_id, text, reply_markup=reply_markup)
        if sent is None:
            return (False, "failed_permanently")
        await asyncio.sleep(0.05)  # Telegram rate limit: max 20 msgs/sec

        logger.info(
            f"trial_notification_sent: user={telegram_id}, notification={notification_key}, "
            f"has_button={has_button}"
        )
        
        return (True, "sent")
    except Exception as e:
        logger.error(
            f"trial_notification_failed: user={telegram_id}, notification={notification_key}, "
            f"error={str(e)}"
        )
        return (False, "failed_temporary")


# ── N-05: «Пробный доступ завершён, −30%» (docs/notifications/bugs-and-risks.md) ──
# Sent exactly once per user by whichever worker expires the trial first
# (fast_expiry_cleanup usually wins the race: 1 min vs 5 min). The 30% is a real
# personal discount (user_discounts — same mechanism as the 15% buttons), applied
# by calculate_final_price at checkout for TRIAL_EXPIRED_DISCOUNT_DAYS.
TRIAL_EXPIRED_DISCOUNT_PERCENT = 30
TRIAL_EXPIRED_DISCOUNT_DAYS = 7


async def claim_trial_expired_notice(telegram_id: int, conn) -> bool:
    """Atomically claim the one-per-user "trial ended" notice.

    True → users.trial_completed_sent went FALSE→TRUE just now and the caller
    must send it (send_trial_expired_notice). False → not a trial user or
    already claimed by the other worker.
    """
    should_send, reason = await trial_service.should_send_completion_notification(
        telegram_id=telegram_id, conn=conn,
    )
    if not should_send:
        logger.debug(f"trial_completion_notification_skipped: user={telegram_id}, reason={reason}")
        return False
    claimed = await trial_service.mark_trial_completed(telegram_id=telegram_id, conn=conn)
    if not claimed:
        logger.info(f"trial_expired_skipped: user={telegram_id}, reason=already_sent")
    return claimed


async def _grant_trial_expired_discount(telegram_id: int) -> None:
    """Apply the promised 30% for TRIAL_EXPIRED_DISCOUNT_DAYS; keep a bigger one."""
    try:
        existing = await database.get_user_discount(telegram_id)
        if existing and int(existing.get("discount_percent") or 0) >= TRIAL_EXPIRED_DISCOUNT_PERCENT:
            return
        await database.create_user_discount(
            telegram_id=telegram_id,
            discount_percent=TRIAL_EXPIRED_DISCOUNT_PERCENT,
            expires_at=datetime.now(timezone.utc) + timedelta(days=TRIAL_EXPIRED_DISCOUNT_DAYS),
            created_by=0,  # system
        )
    except Exception as e:
        logger.warning("trial_expired: discount not applied user=%s err=%s", telegram_id, type(e).__name__)


async def notify_trial_expired(bot: Bot, telegram_id: int) -> bool:
    """Claim (short connection) + send «trial ended» for a trial that has just
    ended and committed — for code that has no connection of its own
    (check_and_disable_expired_subscription). Exactly once per user: the same
    users.trial_completed_sent claim as both workers. Never raises."""
    try:
        pool = await database.get_pool()
        if pool is None:
            return False
        async with pool.acquire() as conn:
            claimed = await claim_trial_expired_notice(telegram_id, conn)
        if not claimed:
            return False
        return await send_trial_expired_notice(bot, telegram_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("trial_expired: notice failed user=%s err=%s", telegram_id, type(e).__name__)
        return False


async def send_trial_expired_notice(bot: Bot, telegram_id: int) -> bool:
    """Apply the discount, then send "trial ended". Call only after a True claim."""
    await _grant_trial_expired_discount(telegram_id)
    language = await resolve_user_language(telegram_id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n.get_text(language, "trial.expired_discount_btn"),
            callback_data="menu_buy_vpn",
        )]
    ])
    sent = await safe_send_message(
        bot, telegram_id, i18n.get_text(language, "trial.expired"),
        parse_mode="HTML", reply_markup=keyboard,
    )
    if not sent:
        logger.warning(f"TRIAL_EXPIRED_SKIP_CHAT_NOT_FOUND user={telegram_id}")
        return False
    await asyncio.sleep(0.05)
    logger.info(f"trial_expired: notification sent: user={telegram_id}")
    return True


# Owner, 2026-09-14: a trial user who BOUGHT bypass GB (the 3-day gift of a GB
# purchase from «Только обход блокировок», or GB bought during the trial) keeps
# the GB after the premium ends — «VPN перестанет работать» would be false.
# Such users get the GB-aware variant of the pre-expiry reminders.
_GB_REMINDER_KEYS = {
    "trial.reminder_24h": "trial.reminder_24h_gb",
    "trial.reminder_3h": "trial.reminder_3h_gb",
    "trial.notification_71h": "trial.notification_71h_gb",
}
_PURCHASED_GB_SQL = """
    SELECT EXISTS (
        SELECT 1 FROM payments
        WHERE telegram_id = $1 AND status = 'approved'
          AND tariff ~ '^(bypass|traffic)_[0-9]+gb$'
    )
"""


async def _gb_reminder_text(pool, telegram_id: int, key: str, language: str, **fmt):
    """GB-aware reminder text for a trial user with purchased GB, else None.
    `fmt` fills the text's placeholders (trial.reminder_3h_gb: {deadline})."""
    gb_key = _GB_REMINDER_KEYS.get(key)
    if gb_key is None:
        return None
    try:
        async with pool.acquire() as conn:
            has_gb = await conn.fetchval(_PURCHASED_GB_SQL, telegram_id)
    except Exception as e:
        logger.warning("trial_reminder_gb_check_failed: user=%s %s: %s", telegram_id, type(e).__name__, e)
        return None
    return i18n.get_text(language, gb_key, **fmt) if has_gb else None


async def _process_single_trial_notification(bot: Bot, pool, row: dict, now: datetime):
    """Process trial notifications for a single user. Acquires and releases DB connection internally."""
    telegram_id = row["telegram_id"]
    trial_expires_at = database._from_db_utc(row.get("trial_expires_at")) if row.get("trial_expires_at") else None
    subscription_expires_at = database._from_db_utc(row.get("subscription_expires_at")) if row.get("subscription_expires_at") else None
    paid_subscription_expires_at = database._from_db_utc(row.get("paid_subscription_expires_at")) if row.get("paid_subscription_expires_at") else None

    if paid_subscription_expires_at:
        logger.info(
            f"trial_expired_skipped_due_to_active_paid_subscription: "
            f"telegram_id={telegram_id}, trial_expires_at={trial_expires_at.isoformat() if trial_expires_at else None}, "
            f"paid_subscription_expires_at={paid_subscription_expires_at.isoformat() if paid_subscription_expires_at else None}, "
            "reason=active_paid_subscription_exists"
        )
        return

    if not trial_expires_at or not subscription_expires_at:
        return

    # === NEW TRIAL NOTIFICATION SCHEDULE (24h and 3h before expiry) ===
    timing = trial_service.calculate_trial_timing(trial_expires_at, now)
    hours_until_expiry = timing["hours_until_expiry"]
    hours_since_activation = timing["hours_since_activation"]

    # ─── Bypass-activated (~5 минут после активации) ────────────────────
    # Основной путь — фаст-таск из callback_activate_trial (см.
    # app/services/trials/bypass_activation_delay.py). Здесь — backup:
    # ловит юзеров, у которых бот перезапустился между активацией и
    # 5-минутным sleep. try_send_bypass_activated идемпотентен через
    # UPDATE ... WHERE flag=FALSE RETURNING id.
    if (
        hours_since_activation >= 5 / 60
        and hours_until_expiry > 1
        and not row.get("trial_notif_bypass_activated_sent", False)
    ):
        try:
            from app.services.trials.bypass_activation_delay import (
                try_send_bypass_activated,
            )
            await try_send_bypass_activated(bot, telegram_id)
        except Exception as e:
            logger.warning(
                "scheduler bypass_activated fallback failed user=%s: %s",
                telegram_id, e,
            )
        return

    # Trial 24h reminder — окно берётся из automated_notifications
    # (по умолчанию 24h ±1h). Админ может изменить в дашборде.
    from app.services.automated_notifications import (
        is_notification_enabled, get_notification_text,
        log_notification_send, get_trigger_config,
    )
    cfg_24h = await get_trigger_config("trial.reminder_24h") or {}
    _target_24h = float(cfg_24h.get("before_expiry_hours") or 24)
    _tol_24h = float(cfg_24h.get("tolerance_hours") or 1)
    if (_target_24h - _tol_24h) <= hours_until_expiry <= (_target_24h + _tol_24h):
        if not row.get("trial_notif_24h_sent", False):
            # Если админ выключил уведомление — только пометим флаг
            # (чтобы не спамили каждый цикл) и залогируем skipped.
            if not await is_notification_enabled("trial.reminder_24h"):
                flag_query = _get_trial_flag_query("trial_notif_24h_sent")
                async with pool.acquire() as conn:
                    await conn.execute(flag_query, telegram_id)
                await log_notification_send(
                    "trial.reminder_24h", telegram_id, status="skipped_disabled",
                )
                logger.info(f"trial_reminder_24h_skipped_disabled: user={telegram_id}")
                return
            # Кастомный текст админа (fallback на i18n при отсутствии).
            language = await resolve_user_language(telegram_id)
            custom = await get_notification_text("trial.reminder_24h", language=language)
            text = (await _gb_reminder_text(pool, telegram_id, "trial.reminder_24h", language)
                    or custom or i18n.get_text(language, "trial.reminder_24h"))
            keyboard = get_trial_buy_keyboard(language)
            success, status = await send_trial_notification(
                bot, pool, telegram_id, "trial.reminder_24h",
                has_button=True, custom_text=text,
            )
            if success or status == "failed_permanently":
                flag_query = _get_trial_flag_query("trial_notif_24h_sent")
                async with pool.acquire() as conn:
                    await conn.execute(flag_query, telegram_id)
                await log_notification_send(
                    "trial.reminder_24h", telegram_id,
                    status="sent" if success else "failed",
                )
                logger.info(f"trial_reminder_24h_sent: user={telegram_id}, hours_until_expiry={hours_until_expiry:.1f}")
            return

    # Trial 3h reminder — with 15% discount. Окно из trigger_config
    # (по умолчанию 3h ±1h).
    cfg_3h = await get_trigger_config("trial.reminder_3h") or {}
    _target_3h = float(cfg_3h.get("before_expiry_hours") or 3)
    _tol_3h = float(cfg_3h.get("tolerance_hours") or 1)
    _lo_3h = _target_3h - _tol_3h if _tol_3h else _target_3h - 0.5
    _hi_3h = _target_3h + (_tol_3h if _tol_3h else 0.5)
    if _lo_3h <= hours_until_expiry <= _hi_3h:
        if not row.get("trial_notif_3h_sent", False):
            if not await is_notification_enabled("trial.reminder_3h"):
                flag_query = _get_trial_flag_query("trial_notif_3h_sent")
                async with pool.acquire() as conn:
                    await conn.execute(flag_query, telegram_id)
                await log_notification_send(
                    "trial.reminder_3h", telegram_id, status="skipped_disabled",
                )
                logger.info(f"trial_reminder_3h_skipped_disabled: user={telegram_id}")
                return
            language = await resolve_user_language(telegram_id)
            # Owner 2026-09-14: the trial's ONE 72 h −15 % window opens here; the
            # text names its end (MSK) instead of «до конца триала» (the button
            # used to give 7 days). Users with bought GB keep the GB wording.
            from database.subscriptions import claim_special_offer
            from app.services.notifications.special_offer import format_deadline
            offer = await claim_special_offer(telegram_id, trial_expires_at)
            deadline = format_deadline(language, offer["expires_at"] if offer else trial_expires_at)
            custom = await get_notification_text("trial.reminder_3h", language=language, params={"deadline": deadline})
            text = (await _gb_reminder_text(pool, telegram_id, "trial.reminder_3h", language, deadline=deadline)
                    or custom or i18n.get_text(language, "trial.reminder_3h", deadline=deadline))
            keyboard = get_trial_discount_keyboard(language)
            photo_id = _REMINDER_3H_PHOTO.get("prod" if config.IS_PROD else "stage", "")
            sent = await _safe_send_photo_or_text(
                bot, telegram_id, photo_id, text, reply_markup=keyboard,
            )
            if sent is not None:
                await asyncio.sleep(0.05)
                flag_query = _get_trial_flag_query("trial_notif_3h_sent")
                async with pool.acquire() as conn:
                    await conn.execute(flag_query, telegram_id)
                await log_notification_send(
                    "trial.reminder_3h", telegram_id, status="sent",
                )
                logger.info(f"trial_reminder_3h_sent: user={telegram_id}, hours_until_expiry={hours_until_expiry:.1f}, discount=15%")
            else:
                await log_notification_send(
                    "trial.reminder_3h", telegram_id, status="blocked",
                )
            # If sent is None (blocked/unreachable) — do NOT mark flag, retry next cycle
            return

    # === LEGACY TRIAL NOTIFICATION SCHEDULE (kept for backward compatibility) ===

    # Phase 1: Read phase — collect all decisions with a short-lived DB connection
    should_send_final = False
    reason_final = None
    payload_final = None
    final_reminder_config = None
    pending_notifications: list[tuple] = []

    async with pool.acquire() as conn:
        try:
            # TOCTOU: Re-check active paid subscription (may have been bought after batch fetch)
            active_paid = await database.get_active_paid_subscription(conn, telegram_id, now)
            if active_paid:
                logger.debug("trial_notification skipped due to active paid subscription")
                return

            final_reminder_config = trial_service.get_final_reminder_config()
            final_reminder_sent = row.get(final_reminder_config["db_flag"], False)
            should_send_final, reason_final = await trial_service.should_send_final_reminder(
                telegram_id=telegram_id,
                trial_expires_at=trial_expires_at,
                subscription_expires_at=subscription_expires_at,
                final_reminder_sent=final_reminder_sent,
                now=now,
                conn=conn
            )
            if should_send_final:
                payload_final = trial_service.prepare_notification_payload(
                    notification_key=final_reminder_config["notification_key"],
                    has_button=final_reminder_config["has_button"]
                )
            elif reason_final:
                logger.debug(
                    f"trial_reminder_skipped: user={telegram_id}, notification=final_6h_before_expiry, "
                    f"reason={reason_final}"
                )

            if not should_send_final:
                notification_flags = {
                    "trial_notif_6h_sent": row.get("trial_notif_6h_sent", False),
                    "trial_notif_60h_sent": row.get("trial_notif_60h_sent", False),
                    "trial_notif_71h_sent": row.get("trial_notif_71h_sent", False),
                }
                for notification in TRIAL_NOTIFICATION_SCHEDULE:
                    try:
                        should_send, reason = await trial_service.should_send_notification(
                            telegram_id=telegram_id,
                            trial_expires_at=trial_expires_at,
                            subscription_expires_at=subscription_expires_at,
                            notification_schedule=notification,
                            notification_flags=notification_flags,
                            now=now,
                            conn=conn
                        )
                        if not should_send:
                            if reason:
                                logger.debug(
                                    f"trial_reminder_skipped: user={telegram_id}, notification={notification['key']}, "
                                    f"reason={reason}"
                                )
                            continue
                        payload = trial_service.prepare_notification_payload(
                            notification_key=notification["key"],
                            has_button=notification["has_button"]
                        )
                        db_flag = notification.get("db_flag", f"trial_notif_{notification['hours']}h_sent")
                        pending_notifications.append((notification, payload, db_flag))
                        # Optimistically mark sent to prevent duplicate sends within this run
                        notification_flags[db_flag] = True
                    except trial_service.TrialServiceError as e:
                        logger.warning(
                            f"trial_reminder_skipped: user={telegram_id}, notification={notification['key']}, "
                            f"service_error={type(e).__name__}: {str(e)}"
                        )
                        continue
        except trial_service.TrialServiceError as e:
            logger.warning(
                f"trial_reminder_skipped: user={telegram_id}, notification=final_6h_before_expiry, "
                f"service_error={type(e).__name__}: {str(e)}"
            )
            return
    # conn released — Telegram I/O below does NOT hold a DB connection

    # Phase 2+3: Telegram I/O then DB write for final reminder
    if should_send_final:
        from app.services.automated_notifications import (
            is_notification_enabled, get_notification_text,
            log_notification_send,
        )
        _key = payload_final["notification_key"]
        # Если админ отключил в дашборде — пометим flag и skip.
        if not await is_notification_enabled(_key):
            async with pool.acquire() as conn:
                await conn.execute(
                    _get_trial_flag_query(final_reminder_config['db_flag']),
                    telegram_id,
                )
            await log_notification_send(_key, telegram_id, status="skipped_disabled")
            logger.info(f"trial_final_reminder_skipped_disabled: user={telegram_id}, key={_key}")
            return
        _lang = await resolve_user_language(telegram_id)
        _custom = (await _gb_reminder_text(pool, telegram_id, _key, _lang)
                   or await get_notification_text(_key, language=_lang))
        success, status = await send_trial_notification(
            bot, pool, telegram_id, _key, payload_final["has_button"],
            custom_text=_custom,
        )
        timing = trial_service.calculate_trial_timing(trial_expires_at, now)
        async with pool.acquire() as conn:
            final_flag_query = _get_trial_flag_query(final_reminder_config['db_flag'])
            if success:
                await conn.execute(final_flag_query, telegram_id)
                await log_notification_send(_key, telegram_id, status="sent")
                logger.info(
                    f"trial_reminder_sent: user={telegram_id}, notification=final_6h_before_expiry, "
                    f"hours_until_expiry={timing['hours_until_expiry']:.1f}h, sent_at={datetime.now(timezone.utc).isoformat()}"
                )
            elif status == "failed_permanently":
                await conn.execute(final_flag_query, telegram_id)
                await log_notification_send(_key, telegram_id, status="blocked")
                logger.warning(
                    f"trial_reminder_failed_permanently: user={telegram_id}, notification=final_6h_before_expiry, "
                    f"reason=forbidden_or_blocked, failed_at={datetime.now(timezone.utc).isoformat()}, will_not_retry=True"
                )
            else:
                await log_notification_send(_key, telegram_id, status="failed")
                logger.warning(
                    f"trial_reminder_failed_temporary: user={telegram_id}, notification=final_6h_before_expiry, "
                    f"reason=temporary_error, will_retry=True"
                )
        return

    # Phase 2+3: Telegram I/O then DB write for notification schedule
    from app.services.automated_notifications import (
        is_notification_enabled as _is_enabled_impl,
        get_notification_text as _get_text_impl,
        log_notification_send as _log_send_impl,
    )
    for notification, payload, db_flag in pending_notifications:
        _key = payload["notification_key"]
        # Legacy notif-keys like trial.notification_60h/71h — если админ
        # выключил, помечаем flag и пропускаем.
        if not await _is_enabled_impl(_key):
            async with pool.acquire() as conn:
                await conn.execute(_get_trial_flag_query(db_flag), telegram_id)
            await _log_send_impl(_key, telegram_id, status="skipped_disabled")
            logger.info(f"trial_schedule_skipped_disabled: user={telegram_id}, key={_key}")
            continue
        _custom = await _get_text_impl(
            _key, language=await resolve_user_language(telegram_id),
        )
        success, status = await send_trial_notification(
            bot, pool, telegram_id, _key, payload["has_button"],
            custom_text=_custom,
        )
        timing = trial_service.calculate_trial_timing(trial_expires_at, now)
        flag_query = _get_trial_flag_query(db_flag)
        if success:
            async with pool.acquire() as conn:
                await conn.execute(flag_query, telegram_id)
            await _log_send_impl(_key, telegram_id, status="sent")
            logger.info(
                f"trial_reminder_sent: user={telegram_id}, notification={notification['key']}, "
                f"hours_since_activation={timing['hours_since_activation']:.1f}h, sent_at={datetime.now(timezone.utc).isoformat()}"
            )
        elif status == "failed_permanently":
            async with pool.acquire() as conn:
                await conn.execute(flag_query, telegram_id)
            await _log_send_impl(_key, telegram_id, status="blocked")
            logger.warning(
                f"trial_reminder_failed_permanently: user={telegram_id}, notification={notification['key']}, "
                f"reason=forbidden_or_blocked, failed_at={datetime.now(timezone.utc).isoformat()}, "
                f"will_not_retry=True"
            )
        else:
            await _log_send_impl(_key, telegram_id, status="failed")
            logger.warning(
                f"trial_reminder_failed_temporary: user={telegram_id}, notification={notification['key']}, "
                f"reason=temporary_error, will_retry=True"
            )


async def process_trial_notifications(bot: Bot):
    """Обработать все уведомления о trial
    
    Проверяет всех пользователей с активным trial и отправляет уведомления
    согласно расписанию на основе trial_expires_at.
    
    КРИТИЧЕСКИЕ ПРОВЕРКИ:
    - subscription.source == "trial"
    - subscription.status == "active"
    - subscription.expires_at > now
    - у пользователя НЕТ активной paid-подписки
    - уведомление ещё не отправлялось (idempotency)
    """
    if not database.DB_READY:
        return
    
    try:
        pool = await database.get_pool()
        now = datetime.now(timezone.utc)
        now_db = database._to_db_utc(now)
        last_subscription_id = 0
        total_fetched = 0

        # Query strings (same every batch)
        query_with_reachable = """
            SELECT u.telegram_id, u.trial_expires_at,
                       s.id as subscription_id,
                       s.expires_at as subscription_expires_at,
                       s.trial_notif_6h_sent, s.trial_notif_60h_sent, s.trial_notif_71h_sent,
                       COALESCE(s.trial_notif_24h_sent, FALSE) as trial_notif_24h_sent,
                       COALESCE(s.trial_notif_3h_sent, FALSE) as trial_notif_3h_sent,
                       COALESCE(s.trial_notif_bypass_activated_sent, FALSE) as trial_notif_bypass_activated_sent,
                       paid_s.expires_at as paid_subscription_expires_at
                FROM users u
                INNER JOIN subscriptions s ON u.telegram_id = s.telegram_id
                    AND s.source = 'trial'
                    AND s.status = 'active'
                    AND s.expires_at > $1
                LEFT JOIN subscriptions paid_s ON u.telegram_id = paid_s.telegram_id
                    AND paid_s.source != 'trial'
                    AND paid_s.status = 'active'
                    AND paid_s.expires_at > $1
                WHERE u.trial_used_at IS NOT NULL
                  AND u.trial_expires_at IS NOT NULL
                  AND u.trial_expires_at > $1
                  AND COALESCE(u.is_reachable, TRUE) = TRUE
                  AND s.id > $2
            ORDER BY s.id ASC
            LIMIT $3
            """
        fallback_query = """
            SELECT u.telegram_id, u.trial_expires_at,
                       s.id as subscription_id,
                       s.expires_at as subscription_expires_at,
                       s.trial_notif_6h_sent, s.trial_notif_60h_sent, s.trial_notif_71h_sent,
                       COALESCE(s.trial_notif_24h_sent, FALSE) as trial_notif_24h_sent,
                       COALESCE(s.trial_notif_3h_sent, FALSE) as trial_notif_3h_sent,
                       COALESCE(s.trial_notif_bypass_activated_sent, FALSE) as trial_notif_bypass_activated_sent,
                       paid_s.expires_at as paid_subscription_expires_at
                FROM users u
                INNER JOIN subscriptions s ON u.telegram_id = s.telegram_id
                    AND s.source = 'trial'
                    AND s.status = 'active'
                    AND s.expires_at > $1
                LEFT JOIN subscriptions paid_s ON u.telegram_id = paid_s.telegram_id
                    AND paid_s.source != 'trial'
                    AND paid_s.status = 'active'
                    AND paid_s.expires_at > $1
                WHERE u.trial_used_at IS NOT NULL
                  AND u.trial_expires_at IS NOT NULL
                  AND u.trial_expires_at > $1
                  AND s.id > $2
            ORDER BY s.id ASC
            LIMIT $3
            """

        while True:
            async with pool.acquire() as conn:
                try:
                    rows = await conn.fetch(query_with_reachable, now_db, last_subscription_id, BATCH_SIZE)
                except asyncpg.UndefinedColumnError:
                    logger.warning("DB_SCHEMA_OUTDATED: is_reachable missing, trial_notifications fallback to legacy query")
                    rows = await conn.fetch(fallback_query, now_db, last_subscription_id, BATCH_SIZE)

            if not rows:
                break

            total_fetched += len(rows)
            logger.info("[WORKER_ITEMS] worker=trial_notifications fetched=%d (last_id=%d)", len(rows), last_subscription_id)
            if total_fetched > 1000:
                logger.warning("[WORKER_ITEMS] worker=trial_notifications total_fetched=%d > 1000", total_fetched)

            for row in rows:
                await _process_single_trial_notification(bot, pool, dict(row), now)

            last_subscription_id = rows[-1]["subscription_id"]
            await asyncio.sleep(BATCH_YIELD_SLEEP)
    except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
        # RESILIENCE FIX: Temporary DB failures are logged as WARNING, not ERROR
        logger.warning(f"trial_notifications: Database temporarily unavailable in process_trial_notifications: {type(e).__name__}: {str(e)[:100]}")
    except Exception as e:
        # Full traceback в error-логе — на debug-уровне обычно фильтруется.
        logger.exception(
            "trial_notifications: Unexpected error in process_trial_notifications: %s: %s",
            type(e).__name__, str(e)[:200],
        )


async def _claim_notice_for_bypass_only_row(conn, telegram_id: int) -> bool:
    """The trial ended and the user's row is ALREADY bypass-only: another path
    turned the trial row into bypass-only without sending "trial ended"
    (check_and_disable_expired_subscription on a screen open,
    ensure_bypass_only_subscription when GB are bought right after the end).
    Claim the one notice (users.trial_completed_sent, as everywhere). The row
    and the bypass entity are left alone — the GB keep working."""
    bypass_only = await conn.fetchval(
        """SELECT TRUE FROM subscriptions
           WHERE telegram_id = $1 AND status = 'active'
             AND (COALESCE(is_bypass_only, FALSE) OR source = 'bypass_only')""",
        telegram_id,
    )
    if not bypass_only:
        return False
    claimed = await claim_trial_expired_notice(telegram_id, conn)
    if claimed:
        logger.info(f"trial_expired_bypass_only: user={telegram_id} — trial-ended notice claimed, bypass kept")
    return claimed


async def _process_single_trial_expiration(bot: Bot, pool, row: dict, now: datetime):
    """Process expiration for a single trial user. The DB work runs on short
    connections; the panel call and the Telegram send hold none (#17)."""
    if await _expire_single_trial(bot, pool, row, now):
        telegram_id = row["telegram_id"]
        if await send_trial_expired_notice(bot, telegram_id):
            logger.info(f"trial_completed: user={telegram_id}, completed_at={now.isoformat()}")


def _log_paid_skip(telegram_id: int, trial_expires_at, active_paid) -> None:
    paid_expires_at = active_paid["expires_at"]
    logger.info(
        "Trial cleanup skipped: user has active paid subscription; "
        f"telegram_id={telegram_id}, trial_expires_at={trial_expires_at.isoformat() if trial_expires_at else None}, "
        f"paid_expires_at={paid_expires_at.isoformat() if paid_expires_at else None}"
    )


async def _expire_single_trial(bot: Bot, pool, row: dict, now: datetime) -> bool:
    """True → the one "trial ended" notice was claimed (committed); the caller
    sends it with no connection held. Every other outcome is handled here.

    #17 (docs/notifications/matrix.md): the connection used to stay checked out
    of the pool during the panel call (disable_premium_user) and both Telegram
    sends. Now: decide on a short connection → panel call with none held →
    expire the row + claim the notice in one short transaction."""
    telegram_id = row["telegram_id"]
    trial_expires_at = database._from_db_utc(row["trial_expires_at"]) if row["trial_expires_at"] else None

    # Phase 1 — decide (short connection)
    try:
        async with pool.acquire() as conn:
            # PRODUCTION HOTFIX: Trial must NEVER revoke VPN or modify subscription if user has active paid.
            active_paid = await database.get_active_paid_subscription(conn, telegram_id, now)
            if active_paid:
                _log_paid_skip(telegram_id, trial_expires_at, active_paid)
                return False
            should_expire, reason = await trial_service.should_expire_trial(
                telegram_id=telegram_id,
                trial_expires_at=trial_expires_at,
                now=now,
                conn=conn
            )
            if not should_expire:
                logger.debug(f"trial_expiry_skipped: user={telegram_id}, reason={reason}")
                if reason == "no_active_trial_subscription":
                    return await _claim_notice_for_bypass_only_row(conn, telegram_id)
                return False
    except trial_service.TrialServiceError as e:
        logger.warning(f"trial_expiry_skipped: user={telegram_id}, service_error={type(e).__name__}: {str(e)}")
        return False
    except Exception as e:
        logger.exception(f"Error expiring trial subscription for user {telegram_id}: {e}")
        return False

    logger.info(
        f"TRIAL_EXPIRATION_EXECUTED: "
        f"telegram_id={telegram_id}, trial_expires_at={trial_expires_at.isoformat() if trial_expires_at else None}, "
        f"decision=EXECUTED"
    )

    # Phase 2 — the panel, with no connection held. 3.x: the premium entity is
    # disabled after the trial (the bypass entity, if any, stays — below).
    try:
        from app.services import remnawave_premium
        await remnawave_premium.disable_premium_user(telegram_id)
        logger.info("trial_expired: Remnawave premium disabled tg=%s", telegram_id)
    except Exception as e:
        logger.warning("trial_expired: disable_premium_user failed tg=%s err=%s", telegram_id, e)

    # Phase 3 — expire the row and claim the notice (one short transaction)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                active_paid = await database.get_active_paid_subscription(conn, telegram_id, now)
                if active_paid:
                    _log_paid_skip(telegram_id, trial_expires_at, active_paid)
                    return False
                # Check if user has Remnawave bypass traffic — keep it active
                has_remnawave = await conn.fetchval(
                    "SELECT remnawave_uuid FROM subscriptions WHERE telegram_id = $1 AND remnawave_uuid IS NOT NULL",
                    telegram_id,
                )
                if has_remnawave:
                    # Transition to bypass-only: remove the premium key, keep Remnawave and active status
                    from datetime import timedelta
                    far_future = database._to_db_utc(now + timedelta(days=3650))
                    await conn.execute("""
                        UPDATE subscriptions
                        SET uuid = NULL, vpn_key = NULL, vpn_key_plus = NULL,
                            is_bypass_only = TRUE,
                            expires_at = $2,
                            source = 'bypass_only'
                        WHERE telegram_id = $1 AND source = 'trial' AND status = 'active'
                    """, telegram_id, far_future)
                    logger.info(f"trial_expired: TRANSITION_TO_BYPASS_ONLY user={telegram_id} — Remnawave stays active")
                    # Extend Remnawave expiry so bypass keeps working (background task)
                    try:
                        from app.services.remnawave_service import extend_remnawave_for_bypass_bg
                        extend_remnawave_for_bypass_bg(telegram_id)
                    except Exception as rmn_err:
                        logger.warning(f"REMNAWAVE_BYPASS_EXTEND_FAIL: tg={telegram_id} {rmn_err}")
                    # The trial end is ONE message — «пробный завершён» (#1):
                    # no «основная подписка закончилась» (and no −15 %) on top of it.
                else:
                    await conn.execute("""
                        UPDATE subscriptions
                        SET status = 'expired', uuid = NULL, vpn_key = NULL
                        WHERE telegram_id = $1 AND source = 'trial' AND status = 'active'
                    """, telegram_id)
                # N-05: same exactly-once claim as fast_expiry_cleanup.
                return await claim_trial_expired_notice(telegram_id, conn)
    except trial_service.TrialServiceError as e:
        logger.warning(f"trial_expiry_skipped: user={telegram_id}, service_error={type(e).__name__}: {str(e)}")
    except Exception as e:
        logger.exception(f"Error expiring trial subscription for user {telegram_id}: {e}")
    return False


async def expire_trial_subscriptions(bot: Bot):
    """Завершить истёкшие trial-подписки
    
    Trial рассматривается как временный флаг, не как источник прав доступа.
    Защита: trial НИКОГДА не отменяет подписку, у которой source != 'trial' и expires_at > now().
    
    Когда trial_expires_at <= now:
    - Проверяет наличие активной подписки с source != 'trial' и expires_at > now()
    - Если есть — пропуск (не меняем статус, не трогаем VPN, не шлём уведомления), лог trial_expired_skipped_due_to_active_paid_subscription
    - Иначе: помечает trial-подписку как expired, удаляет trial UUID из VPN API, при необходимости шлёт уведомление
    """
    if not database.DB_READY:
        return

    try:
        pool = await database.get_pool()
        now = datetime.now(timezone.utc)
        now_db = database._to_db_utc(now)
        last_telegram_id = 0

        query_with_reachable = """
            SELECT u.telegram_id, u.trial_used_at, u.trial_expires_at,
                   s.uuid, s.expires_at as subscription_expires_at
            FROM users u
            LEFT JOIN subscriptions s ON u.telegram_id = s.telegram_id AND s.source = 'trial' AND s.status = 'active'
            WHERE u.trial_used_at IS NOT NULL
              AND u.trial_expires_at IS NOT NULL
              AND u.trial_expires_at <= $1
              AND u.trial_expires_at > $1 - INTERVAL '24 hours'
              AND COALESCE(u.is_reachable, TRUE) = TRUE
              AND u.telegram_id > $2
            ORDER BY u.telegram_id ASC
            LIMIT $3
        """
        fallback_query = """
            SELECT u.telegram_id, u.trial_used_at, u.trial_expires_at,
                   s.uuid, s.expires_at as subscription_expires_at
            FROM users u
            LEFT JOIN subscriptions s ON u.telegram_id = s.telegram_id AND s.source = 'trial' AND s.status = 'active'
            WHERE u.trial_used_at IS NOT NULL
              AND u.trial_expires_at IS NOT NULL
              AND u.trial_expires_at <= $1
              AND u.trial_expires_at > $1 - INTERVAL '24 hours'
              AND u.telegram_id > $2
            ORDER BY u.telegram_id ASC
            LIMIT $3
        """

        while True:
            async with pool.acquire() as conn:
                try:
                    rows = await conn.fetch(query_with_reachable, now_db, last_telegram_id, BATCH_SIZE)
                except asyncpg.UndefinedColumnError:
                    logger.warning("DB_SCHEMA_OUTDATED: is_reachable missing, expire_trial fallback to legacy query")
                    rows = await conn.fetch(fallback_query, now_db, last_telegram_id, BATCH_SIZE)

            if not rows:
                break

            for row in rows:
                await _process_single_trial_expiration(bot, pool, dict(row), now)

            last_telegram_id = rows[-1]["telegram_id"]
            await asyncio.sleep(BATCH_YIELD_SLEEP)
    except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
        # RESILIENCE FIX: Temporary DB failures are logged as WARNING, not ERROR
        logger.warning(f"trial_notifications: Database temporarily unavailable in expire_trial_subscriptions: {type(e).__name__}: {str(e)[:100]}")
    except Exception as e:
        logger.error(f"trial_notifications: Unexpected error in expire_trial_subscriptions: {type(e).__name__}: {str(e)[:100]}")
        logger.debug("trial_notifications: Full traceback in expire_trial_subscriptions", exc_info=True)


async def run_trial_scheduler(bot: Bot):
    """Основной цикл scheduler для trial-уведомлений
    
    Запускается каждые 5 минут для проверки и отправки уведомлений.
    
    SAFE: Singleton guard предотвращает повторный запуск.
    Если scheduler уже запущен, повторные вызовы игнорируются.
    """
    global _TRIAL_SCHEDULER_STARTED

    # Singleton guard: предотвращаем повторный запуск (task-safe via lock)
    async with _TRIAL_SCHEDULER_LOCK:
        if _TRIAL_SCHEDULER_STARTED:
            logger.warning("Trial notifications scheduler already running, skipping duplicate start")
            return
        _TRIAL_SCHEDULER_STARTED = True
    logger.info("Trial notifications scheduler started")
    from app.core import runtime_health  # dashboard liveness (in-memory)
    runtime_health.register("trial_notifications", interval_s=300 + 120, initial_delay_s=60)
    
    # Prevent worker burst at startup
    jitter_s = random.uniform(5, 60)
    await asyncio.sleep(jitter_s)
    logger.debug("trial_notifications: startup jitter done (%.1fs)", jitter_s)
    
    iteration_number = 0
    
    while True:
        iteration_start_time = time.time()
        iteration_number += 1
        
        # STEP 2.3 — OBSERVABILITY: Structured logging for worker iteration start
        correlation_id = log_worker_iteration_start(
            worker_name="trial_notifications",
            iteration_number=iteration_number
        )
        
        iteration_outcome = "success"
        iteration_error_type = None
        should_exit_loop = False
        
        try:
            # Feature flag check
            from app.core.feature_flags import get_feature_flags
            feature_flags = get_feature_flags()
            if not feature_flags.background_workers_enabled:
                logger.warning(
                    f"[FEATURE_FLAG] Background workers disabled, skipping iteration in trial_notifications "
                    f"(iteration={iteration_number})"
                )
                iteration_outcome = "skipped"
                reason = "background_workers_enabled=false"
                log_worker_iteration_end(
                    worker_name="trial_notifications",
                    outcome=iteration_outcome,
                    items_processed=0,
                    duration_ms=(time.time() - iteration_start_time) * 1000,
                    reason=reason,
                )
                await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
                continue
            
            # Simple DB readiness check
            if not database.DB_READY:
                logger.warning("trial_notifications: skipping — DB not ready")
                iteration_outcome = "skipped"
                await asyncio.sleep(300)  # Sleep before next check
                continue
            
            # H1 fix: Wrap iteration body with timeout
            async def _run_iteration():
                # Обрабатываем уведомления
                await process_trial_notifications(bot)
                # Завершаем истёкшие trial-подписки
                await expire_trial_subscriptions(bot)
            
            try:
                await asyncio.wait_for(_run_iteration(), timeout=120.0)
                iteration_outcome = "success"
            except asyncio.TimeoutError:
                logger.error(
                    "WORKER_TIMEOUT worker=trial_notifications exceeded 120s — iteration cancelled"
                )
                iteration_outcome = "timeout"
                iteration_error_type = "timeout"
            
        except asyncio.CancelledError:
            logger.info("Trial notifications task cancelled")
            iteration_outcome = "cancelled"
            should_exit_loop = True
        except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
            # RESILIENCE FIX: Temporary DB failures don't crash the task loop
            logger.warning(f"trial_notifications: Database temporarily unavailable in scheduler loop: {type(e).__name__}: {str(e)[:100]}")
            iteration_outcome = "degraded"
            iteration_error_type = "infra_error"
        except Exception as e:
            logger.error(f"trial_notifications: Unexpected error in scheduler loop: {type(e).__name__}: {str(e)[:100]}")
            logger.debug("trial_notifications: Full traceback for scheduler loop", exc_info=True)
            iteration_outcome = "failed"
            iteration_error_type = classify_error(e)
            try:
                from app.services.admin_alerts import alert_worker_failure
                await alert_worker_failure(bot, "trial_notifications", e, iteration=iteration_number)
            except Exception:
                pass
        finally:
            # H2 fix: ITERATION_END always fires in finally block
            runtime_health.record("trial_notifications", iteration_outcome, iteration_error_type)
            duration_ms = (time.time() - iteration_start_time) * 1000
            log_worker_iteration_end(
                worker_name="trial_notifications",
                outcome=iteration_outcome,
                items_processed=0,
                error_type=iteration_error_type,
                duration_ms=duration_ms
            )
            if iteration_outcome not in ("success", "cancelled", "skipped"):
                await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
        
        if should_exit_loop:
            break
        
        # Sleep after iteration completes (outside try/finally)
        # Ждём 5 минут до следующей проверки
        await asyncio.sleep(300)
