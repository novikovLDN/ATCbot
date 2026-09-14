"""Модуль для отправки напоминаний об окончании подписки"""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import database
import config
from app import i18n
from app.services.language_service import resolve_user_language
from app.services.notifications import service as notification_service
from app.services.notifications.service import ReminderType
from app.utils.telegram_safe import safe_send_message
from app.utils.logging_helpers import (
    log_worker_iteration_start,
    log_worker_iteration_end,
    classify_error,
)
# import outline_api  # DISABLED - мигрировали на Xray Core (VLESS)


# Photo header for the 3-day-before-expiry reminder. Empty string for stage —
# stage bot can't render the prod file_id, so we silently fall through to
# safe_send_message (no photo).
_REMINDER_3D_PHOTO = {
    "prod": "AgACAgQAAxkBAAFU1DxqGqtH5bNmsjjv1hncBK3mGdiBnQACLA5rG4Qv2VC5BOb25R-UugEAAwIAA3cAAzsE",
    "stage": "",
}

# Idempotency: skip if reminder sent within this window (container restart guard)
REMINDER_IDEMPOTENCY_WINDOW = timedelta(minutes=30)

# #14 (docs/notifications/matrix.md): a pass every 15 min. The 3 h reminder's
# window is 2 h wide (service.should_send_reminder), so it survives a missed
# pass or a restart; at 45 min with a 1 h window one hiccup lost it.
REMINDERS_INTERVAL_SECONDS = 15 * 60

logger = logging.getLogger(__name__)


def get_renewal_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура для продления доступа"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n.get_text(language, "subscription.renew"),
            callback_data="menu_buy_vpn"
        )]
    ])
    return keyboard


def get_renewal_keyboard_7d(language: str) -> InlineKeyboardMarkup:
    """Клавиатура для напоминания за 7 дней"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n.get_text(language, "reminder.paid_7d_btn"),
            callback_data="menu_buy_vpn"
        )],
        [InlineKeyboardButton(
            text=i18n.get_text(language, "main.profile"),
            callback_data="menu_profile"
        )],
    ])


def get_renewal_keyboard_3d(language: str) -> InlineKeyboardMarkup:
    """Клавиатура для напоминания за 3 дня"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n.get_text(language, "reminder.paid_3d_btn"),
            callback_data="menu_buy_vpn"
        )],
    ])


def get_renewal_keyboard_1d(language: str) -> InlineKeyboardMarkup:
    """Клавиатура для напоминания за 1 день"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n.get_text(language, "reminder.paid_1d_btn"),
            callback_data="menu_buy_vpn"
        )],
    ])


def get_renewal_discount_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура со скидкой 15% за 3 часа до окончания подписки"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n.get_text(language, "reminder.paid_3h_discount_btn"),
            callback_data="paid_discount_15"
        )],
        [InlineKeyboardButton(
            text=i18n.get_text(language, "subscription.renew"),
            callback_data="menu_buy_vpn"
        )],
    ])


def _buy_keyboard(language: str, text_key: str) -> InlineKeyboardMarkup:
    """Single CTA keyboard with configurable button text."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n.get_text(language, text_key), callback_data="menu_buy_vpn")]
    ])


def get_subscription_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура для оформления подписки"""
    return _buy_keyboard(language, "main.buy")


def get_tariff_1_month_keyboard(language: str) -> InlineKeyboardMarkup:
    """Клавиатура для подписки на 1 месяц"""
    return _buy_keyboard(language, "main.buy")




# Paid reminders registered in the dashboard (enable toggle, custom text, segment).
_NOTIF_KEYS = {
    ReminderType.REMINDER_7D: "subscription.reminder_7d",
    ReminderType.REMINDER_3D: "subscription.reminder_3d",
    ReminderType.REMINDER_1D: "subscription.reminder_1d",
    ReminderType.REMINDER_24H: "subscription.reminder_24h",
    ReminderType.REMINDER_3H: "subscription.reminder_3h",
}
_AUDIT_MESSAGES = {
    ReminderType.ADMIN_1DAY_6H: "Admin 1-day reminder (6h before expiry)",
    ReminderType.ADMIN_7DAYS_24H: "Admin grant reminder (24h before expiry)",
    ReminderType.REMINDER_7D: "Paid subscription reminder (7d before expiry)",
    ReminderType.REMINDER_3D: "Paid subscription reminder (3d before expiry)",
    ReminderType.REMINDER_1D: "Paid subscription reminder (1d before expiry)",
    ReminderType.REMINDER_24H: "Paid subscription reminder (24h before expiry)",
    ReminderType.REMINDER_3H: "Paid subscription reminder (3h before expiry) with 15% discount",
}


async def _claim_reminder(telegram_id: int, reminder_type: ReminderType, expires_at) -> bool:
    """#16 / #24: claim the reminder for THIS period before sending it."""
    from database.subscriptions import claim_reminder_flag
    flag = notification_service.get_reminder_flag_name(reminder_type)
    return await claim_reminder_flag(telegram_id, flag, expires_at)


async def _release_reminder(telegram_id: int, reminder_type: ReminderType, expires_at) -> None:
    from database.subscriptions import release_reminder_flag
    try:
        await release_reminder_flag(telegram_id, notification_service.get_reminder_flag_name(reminder_type), expires_at)
    except Exception as e:  # noqa: BLE001 — worst case the reminder is lost, never sent twice
        logger.warning("reminder_release_failed: user=%s type=%s %s", telegram_id, reminder_type.value, type(e).__name__)


def _rub(amount) -> str:
    """199 / 169.15 — rubles without a trailing «.00»."""
    return f"{float(amount):.2f}".rstrip("0").rstrip(".")


async def _autorenew_reminder(subscription: dict, language: str):
    """#8: auto-renewal is on → not «продлите» (a second, manual payment) but
    «спишем N ₽» when the balance covers the renewal, else «пополните на N ₽
    до …». None when the quote cannot be made (the usual text then)."""
    import auto_renewal
    from app.services.notifications.special_offer import MSK, format_deadline
    telegram_id = subscription["telegram_id"]
    expires_at = subscription.get("expires_at")
    try:
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            quote = await auto_renewal.renewal_quote(conn, telegram_id, subscription)
        balance = float(await database.get_user_balance(telegram_id) or 0)
    except Exception as e:  # noqa: BLE001
        logger.warning("reminder_autorenew_quote_failed: user=%s %s", telegram_id, type(e).__name__)
        return None
    amount = float(quote["amount_rubles"])
    date = expires_at.astimezone(MSK).strftime("%d.%m.%Y") if expires_at else "—"
    if balance >= amount:
        text = i18n.get_text(language, "reminder.paid_autorenew_ok", date=date,
                             amount=_rub(amount), balance=_rub(balance))
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n.get_text(language, "main.profile"), callback_data="menu_profile")],
        ])
        return text, keyboard
    missing = round(amount - balance, 2)
    text = i18n.get_text(language, "reminder.paid_autorenew_topup", date=date, amount=_rub(amount),
                         balance=_rub(balance), missing=_rub(missing),
                         deadline=format_deadline(language, expires_at) if expires_at else "—")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n.get_text(language, "main.btn_topup_balance"), callback_data="topup_balance")],
        [InlineKeyboardButton(text=i18n.get_text(language, "subscription.renew"), callback_data="menu_buy_vpn")],
    ])
    return text, keyboard


async def _build_reminder(subscription: dict, reminder_type: ReminderType, language: str,
                          notif_key: str | None, *, enabled: bool):
    """(text, keyboard) of one reminder, in the user's language."""
    from app.services.automated_notifications import get_notification_text
    telegram_id = subscription["telegram_id"]
    if (reminder_type in (ReminderType.REMINDER_7D, ReminderType.REMINDER_3D, ReminderType.REMINDER_1D)
            and subscription.get("auto_renew")):
        built = await _autorenew_reminder(subscription, language)
        if built is not None:
            return built
    if reminder_type == ReminderType.ADMIN_1DAY_6H:
        return i18n.get_text(language, "reminder.admin_1day_6h"), get_subscription_keyboard(language)
    if reminder_type == ReminderType.ADMIN_7DAYS_24H:
        from app.services.notifications.special_offer import from_price_rub
        return (i18n.get_text(language, "reminder.admin_7days_24h", price=await from_price_rub()),
                get_tariff_1_month_keyboard(language))
    if reminder_type == ReminderType.REMINDER_7D:
        text = (await get_notification_text(notif_key, language=language)) or i18n.get_text(language, "reminder.paid_7d")
        return text, get_renewal_keyboard_7d(language)
    if reminder_type == ReminderType.REMINDER_3D:
        text = (await get_notification_text(notif_key, language=language)) or i18n.get_text(language, "reminder.paid_3d")
        return text, get_renewal_keyboard_3d(language)
    if reminder_type == ReminderType.REMINDER_1D:
        gb_left = await _bypass_left_text(telegram_id, language)
        if gb_left:
            return i18n.get_text(language, "reminder.paid_1d_gb", remaining=gb_left), get_renewal_keyboard_1d(language)
        text = (await get_notification_text(notif_key, language=language)) or i18n.get_text(language, "reminder.paid_1d")
        return text, get_renewal_keyboard_1d(language)
    if reminder_type == ReminderType.REMINDER_24H:
        text = (await get_notification_text(notif_key, language=language)) or i18n.get_text(language, "reminder.paid_24h")
        return text, get_renewal_keyboard(language)
    if reminder_type == ReminderType.REMINDER_3H:
        # Owner 2026-09-14: this reminder opens the period's ONE 72 h −15 %
        # window (or shows the one already open); the text names its end (MSK).
        # Only when enabled — a disabled reminder never gets here.
        offer = None
        if enabled:
            from database.subscriptions import claim_special_offer
            offer = await claim_special_offer(telegram_id, subscription.get("expires_at"))
        gb_left = await _bypass_left_text(telegram_id, language)
        if offer:
            from app.services.notifications.special_offer import format_deadline
            deadline = format_deadline(language, offer["expires_at"])
            if gb_left:
                text = i18n.get_text(language, "reminder.paid_3h_special_gb", deadline=deadline, remaining=gb_left)
            else:
                text = (await get_notification_text(notif_key, language=language, params={"deadline": deadline})) \
                    or i18n.get_text(language, "reminder.paid_3h_special", deadline=deadline)
            return text, get_renewal_discount_keyboard(language)
        if gb_left:
            return i18n.get_text(language, "reminder.paid_3h_no_offer_gb", remaining=gb_left), get_renewal_keyboard(language)
        return i18n.get_text(language, "reminder.paid_3h_no_offer"), get_renewal_keyboard(language)
    return None, None


async def _bypass_left_text(telegram_id: int, language: str):
    """#9: «VPN перестанет работать» is false when bypass GB remain — they keep
    working after the premium ends. The amount left (from the panel), or None
    (no GB left / panel unavailable → the usual text)."""
    from app.services.subscriptions import live_state
    bypass = await live_state.read_bypass(telegram_id)
    if bypass.works and not bypass.unlimited:
        return live_state.format_bytes(language, bypass.remaining)
    return None


async def _send_reminder(bot: Bot, telegram_id: int, reminder_type: ReminderType, text: str, keyboard):
    """3-day reminder gets a photo header on prod; everything else plain text.
    The photo send falls back to text on any error (stale file_id, blocked …)."""
    sent = None
    if reminder_type == ReminderType.REMINDER_3D:
        photo_id = _REMINDER_3D_PHOTO.get("prod" if config.IS_PROD else "stage", "")
        if photo_id:
            try:
                sent = await bot.send_photo(
                    chat_id=telegram_id, photo=photo_id, caption=text,
                    reply_markup=keyboard, parse_mode="HTML",
                )
            except Exception as e:
                logger.warning(
                    "REMINDER_3D photo send failed user=%s err=%s — falling back to plain text",
                    telegram_id, type(e).__name__,
                )
                sent = None
    if sent is None:
        sent = await safe_send_message(bot, telegram_id, text, reply_markup=keyboard)
    return sent


async def send_smart_reminders(bot: Bot):
    """Отправить напоминания об окончании подписки (платной / бесплатных дней).

    Each reminder: decision on the pass snapshot → dashboard toggle / segment →
    CLAIM for the snapshot's period (#16, #24) → text → send → a temporary
    failure releases the claim (the next pass retries), a block keeps it.
    """
    from app.services.automated_notifications import (
        is_notification_enabled, log_notification_send, get_trigger_config, is_user_in_segment,
    )
    try:
        subscriptions = await database.get_subscriptions_for_reminders()

        if not subscriptions:
            return

        logger.info("Found %d subscriptions for reminders check", len(subscriptions))

        for subscription in subscriptions:
            telegram_id = subscription["telegram_id"]

            try:
                # Use notification service to determine if reminder should be sent
                decision = notification_service.should_send_reminder(subscription)

                if not decision.should_send:
                    if decision.reason:
                        logger.debug("Skipping reminder for user %s: %s", telegram_id, decision.reason)
                    continue

                # Idempotency: skip if reminder sent recently (container restart guard)
                last_reminder_at = subscription.get("last_reminder_at")
                if last_reminder_at and isinstance(last_reminder_at, datetime):
                    try:
                        # subscription from get_subscriptions_for_reminders is normalized (aware UTC via _from_db_utc)
                        last_at = last_reminder_at if last_reminder_at.tzinfo else last_reminder_at.replace(tzinfo=timezone.utc)
                        delta = datetime.now(timezone.utc) - last_at
                        if 0 <= delta.total_seconds() < REMINDER_IDEMPOTENCY_WINDOW.total_seconds():
                            logger.debug("Skipping reminder for user %s: last_reminder_at within idempotency window", telegram_id)
                            continue
                    except (TypeError, AttributeError) as e:
                        logger.warning("Invalid last_reminder_at for user %s: %s", telegram_id, e)

                reminder_type = decision.reminder_type
                notif_key = _NOTIF_KEYS.get(reminder_type)

                # Disabled in the dashboard — mark it for this period and skip.
                if notif_key and not await is_notification_enabled(notif_key):
                    try:
                        await notification_service.mark_reminder_sent(telegram_id, reminder_type)
                        await log_notification_send(notif_key, telegram_id, status="skipped_disabled")
                    except Exception:
                        pass
                    logger.info("reminder_skipped_disabled: user=%s key=%s", telegram_id, notif_key)
                    continue

                # Optional segment_filter from the dashboard trigger config: not in
                # the segment now → skip this pass WITHOUT marking (may enter later).
                if notif_key:
                    _tcfg = await get_trigger_config(notif_key) or {}
                    _seg = str(_tcfg.get("segment_filter") or "").strip()
                    if _seg and not await is_user_in_segment(telegram_id, _seg):
                        try:
                            await log_notification_send(notif_key, telegram_id, status="skipped_disabled")
                        except Exception:
                            pass
                        logger.info("reminder_skipped_segment: user=%s key=%s seg=%s", telegram_id, notif_key, _seg)
                        continue

                # #16 / #24: claim for the period this pass saw. A renewal since
                # the snapshot (new expires_at, flags reset) → nothing to claim.
                expires_at = subscription.get("expires_at")
                if not await _claim_reminder(telegram_id, reminder_type, expires_at):
                    logger.info("reminder_skipped_not_claimed: user=%s type=%s", telegram_id, reminder_type.value)
                    continue

                language = await resolve_user_language(telegram_id)
                text, keyboard = await _build_reminder(subscription, reminder_type, language, notif_key, enabled=True)
                if not (text and keyboard):
                    await _release_reminder(telegram_id, reminder_type, expires_at)
                    continue

                sent = await _send_reminder(bot, telegram_id, reminder_type, text, keyboard)
                if sent is None:
                    if await database.subscriptions.is_user_blocked(telegram_id):
                        status = "blocked"          # blocked the bot: never retried
                    else:
                        status = "failed"           # transient: the next pass retries
                        await _release_reminder(telegram_id, reminder_type, expires_at)
                    if notif_key:
                        try:
                            await log_notification_send(notif_key, telegram_id, status=status)
                        except Exception:
                            pass
                    continue
                await asyncio.sleep(0.05)  # Telegram rate limit: max 20 msgs/sec

                if notif_key:
                    try:
                        await log_notification_send(notif_key, telegram_id, status="sent")
                    except Exception:
                        pass

                await database._log_audit_event_atomic_standalone(
                    "reminder_sent",
                    telegram_id,
                    telegram_id,
                    _AUDIT_MESSAGES.get(reminder_type, reminder_type.value),
                )

                logger.info("Reminder (%s) sent to user %s", reminder_type.value, telegram_id)

            except Exception as e:
                # Ошибка для одного пользователя не должна ломать цикл
                logger.error("Error sending reminder to user %s: %s", telegram_id, e, exc_info=True)
                continue

    except Exception as e:
        logger.exception(f"Error in send_smart_reminders: {e}")


async def reminders_task(bot: Bot):
    """Фоновая задача для отправки напоминаний об окончании подписки (выполняется каждые 30-60 минут)"""
    from app.core import runtime_health  # dashboard liveness (in-memory)
    runtime_health.register("reminders", interval_s=REMINDERS_INTERVAL_SECONDS + 120, initial_delay_s=60)
    # Небольшая задержка при старте, чтобы БД успела инициализироваться
    await asyncio.sleep(60)

    iteration_number = 0
    while True:
        iteration_number += 1
        iteration_start_time = time.time()
        
        # H1+H2 fix: Add iteration start logging and timeout wrapper
        correlation_id = log_worker_iteration_start(
            worker_name="reminders",
            iteration_number=iteration_number
        )
        
        iteration_outcome = "success"
        iteration_error_type = None
        
        try:
            # H1 fix: Wrap iteration body with timeout
            async def _run_iteration():
                await send_smart_reminders(bot)
            
            try:
                await asyncio.wait_for(_run_iteration(), timeout=120.0)
            except asyncio.TimeoutError:
                logger.error(
                    "WORKER_TIMEOUT worker=reminders exceeded 120s — iteration cancelled"
                )
                iteration_outcome = "timeout"
                iteration_error_type = "timeout"
        except asyncio.CancelledError:
            logger.info("Reminders task cancelled")
            iteration_outcome = "cancelled"
            raise
        except Exception as e:
            logger.error("reminders: Unexpected error in task loop: %s: %.100s", type(e).__name__, str(e))
            logger.debug("reminders: Full traceback for task loop", exc_info=True)
            iteration_outcome = "failed"
            iteration_error_type = classify_error(e)
            try:
                from app.services.admin_alerts import alert_worker_failure
                await alert_worker_failure(bot, "reminders", e, iteration=iteration_number)
            except Exception:
                pass
        finally:
            # H2 fix: ITERATION_END always fires in finally block
            runtime_health.record("reminders", iteration_outcome, iteration_error_type)
            duration_ms = int((time.time() - iteration_start_time) * 1000)
            log_worker_iteration_end(
                worker_name="reminders",
                outcome=iteration_outcome,
                items_processed=0,
                error_type=iteration_error_type,
                duration_ms=duration_ms,
            )
        
        if iteration_outcome == "cancelled":
            break
        
        await asyncio.sleep(REMINDERS_INTERVAL_SECONDS)
