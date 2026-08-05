"""Модуль для отправки напоминаний об окончании подписки"""
import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import database
import config
from app import i18n
from app.services.language_service import resolve_user_language
from app.core.feature_flags import background_workers_paused
from app.core.worker_startup import startup_jitter
from app.services.notifications import service as notification_service
from app.services.notifications.service import ReminderType
from app.utils.telegram_safe import safe_send_message
from app.core.structured_logger import log_event
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

# ──────────────────────────────────────────────────────────────────────────
#  Бюджет времени на одну итерацию напоминаний
# ──────────────────────────────────────────────────────────────────────────
#
# ЧТО БЫЛО. Итерация целиком оборачивалась в asyncio.wait_for(..., 120 с) —
# одно число на любой размер выборки. Выборка кандидатов теперь идёт в SQL
# страницами по 500 строк с потолком 20000 (database/reminders_queries.py),
# и 120 секунд на неё физически не хватает: на каждого, кому реально уходит
# напоминание, приходится resolve_user_language, чтение текста из дашборда,
# отправка с паузой 0.05 с под лимит Telegram, отметка флага и запись в
# audit_log — порядка 0.1 секунды даже без сетевых задержек.
#
# ЧЕМ ЭТО ПЛОХО ИМЕННО ЗДЕСЬ. wait_for отменяет корутину целиком, посреди
# прохода, без единого лога о том, сколько успели. А выборка идёт ORDER BY id,
# то есть обрывается всегда один и тот же хвост — эти люди не получают
# напоминаний систематически, а в логах видно только «iteration cancelled».
#
# ЧТО ТЕПЕРЬ. Два разных ограничителя:
#   • мягкий бюджет — считается от РАЗМЕРА ВЫБОРКИ, проверяется в цикле,
#     на исчерпании цикл выходит сам и пишет REMINDERS_BACKLOG с числами;
#     недоделанные кандидаты не теряются — флаги им не выставлены, и на
#     следующем витке (через 45 минут) они снова в выборке;
#   • жёсткий wait_for — страховка от зависшего await (Telegram или пул),
#     который мягкую проверку просто не достигнет. Он заведомо больше
#     мягкого, чтобы штатным путём выхода был мягкий, а не отмена.
REMINDERS_BASE_ITERATION_SECONDS = 60.0
# Верхняя оценка работы на одного кандидата выборки.
REMINDERS_SECONDS_PER_CANDIDATE = 0.1
# Потолок мягкого бюджета. При интервале воркера в 45 минут 10 минут работы —
# это ещё с большим запасом, но уже вчетверо больше прежних 120 секунд.
REMINDERS_MAX_ITERATION_SECONDS = float(
    os.getenv("REMINDERS_MAX_ITERATION_SECONDS", "600")
)
# Запас поверх мягкого бюджета: столько отводится на саму выборку и на то,
# чтобы цикл успел заметить исчерпание бюджета и корректно выйти.
REMINDERS_ITERATION_HARD_TIMEOUT = REMINDERS_MAX_ITERATION_SECONDS + 120.0

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


async def _load_paid_reminder_trigger_configs() -> dict:
    """Прочитать окна напоминаний из дашборда — один раз на проход.

    Раньше окна были зашиты в should_send_reminder, и тумблеры
    before_expiry_hours / tolerance_hours в дашборде ни на что не влияли.
    Читаем здесь, а не внутри цикла: конфигов всего четыре, а подписок могут
    быть десятки тысяч — на пользователя это лишний поход в базу.

    Ошибка чтения не должна ронять проход: возвращаем то, что успели, а
    should_send_reminder на недостающих ключах отработает на дефолтах.
    """
    from app.services.automated_notifications import get_trigger_config
    out: dict = {}
    for key in notification_service.PAID_REMINDER_NOTIFICATION_KEYS.values():
        try:
            out[key] = await get_trigger_config(key) or {}
        except Exception as e:
            logger.warning("reminders: trigger_config read failed key=%s: %s", key, e)
    return out


async def send_smart_reminders(bot: Bot) -> int:
    """Отправить умные напоминания пользователям.

    Возвращает число отправленных сообщений — оно уходит в items_processed
    метрики итерации. Раньше туда безусловно писался ноль, и по ITERATION_END
    нельзя было отличить «никому не пора» от «выборка была, но мы её не
    разгребли».
    """
    sent_count = 0
    try:
        # Сначала конфиг окон, потом выборка: отбор кандидатов в SQL идёт по
        # тем же числам, по которым потом решает should_send_reminder.
        trigger_configs = await _load_paid_reminder_trigger_configs()
        subscriptions = await database.get_subscriptions_for_reminders(
            windows=notification_service.reminder_query_windows(trigger_configs),
        )

        if not subscriptions:
            return sent_count

        logger.info("Found %d subscriptions for reminders check", len(subscriptions))

        # Мягкий бюджет считаем от фактического размера выборки: на пустой
        # день это минута, на разгребание потолка в 20000 строк — десять.
        iteration_start = time.monotonic()
        budget_seconds = min(
            REMINDERS_MAX_ITERATION_SECONDS,
            REMINDERS_BASE_ITERATION_SECONDS
            + len(subscriptions) * REMINDERS_SECONDS_PER_CANDIDATE,
        )
        checked = 0

        for subscription in subscriptions:
            if time.monotonic() - iteration_start > budget_seconds:
                # Выходим сами, до того как сработает жёсткий wait_for.
                # Иначе итерацию отменяют посреди прохода и в логах остаётся
                # только «iteration cancelled» без единой цифры.
                logger.warning(
                    "REMINDERS_BACKLOG checked=%s of=%s sent=%s budget=%.0fs — "
                    "выборка не разобрана за итерацию, остаток уйдёт в следующую",
                    checked, len(subscriptions), sent_count, budget_seconds,
                )
                break
            checked += 1
            telegram_id = subscription["telegram_id"]

            try:
                # Use notification service to determine if reminder should be sent
                decision = notification_service.should_send_reminder(
                    subscription, trigger_configs=trigger_configs,
                )

                if not decision.should_send:
                    # Skip this subscription (already sent, not in time window, etc.)
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

                language = await resolve_user_language(telegram_id)

                # Determine reminder text and keyboard based on reminder type.
                # Для paid-reminder'ов (7d/3d/1d/24h/3h) сначала спрашиваем
                # automated_notifications — если админ отключил через дашборд,
                # пропускаем + логируем как skipped_disabled. Кастомный текст
                # админа берётся первым, fallback на i18n-дефолт.
                from app.services.automated_notifications import (
                    is_notification_enabled, get_notification_text,
                    log_notification_send,
                )
                reminder_type = decision.reminder_type
                text = None
                keyboard = None
                audit_message = None
                notif_key = None  # registry-key для логирования и enable-check

                if reminder_type == ReminderType.ADMIN_1DAY_6H:
                    text = i18n.get_text(language, "reminder.admin_1day_6h")
                    keyboard = get_subscription_keyboard(language)
                    audit_message = "Admin 1-day reminder (6h before expiry)"

                elif reminder_type == ReminderType.ADMIN_7DAYS_24H:
                    text = i18n.get_text(language, "reminder.admin_7days_24h")
                    keyboard = get_tariff_1_month_keyboard(language)
                    audit_message = "Admin 7-day reminder (24h before expiry)"

                elif reminder_type == ReminderType.REMINDER_7D:
                    notif_key = "subscription.reminder_7d"
                    text = (await get_notification_text(notif_key, language=language)) or i18n.get_text(language, "reminder.paid_7d")
                    keyboard = get_renewal_keyboard_7d(language)
                    audit_message = "Paid subscription reminder (7d before expiry)"

                elif reminder_type == ReminderType.REMINDER_3D:
                    notif_key = "subscription.reminder_3d"
                    text = (await get_notification_text(notif_key, language=language)) or i18n.get_text(language, "reminder.paid_3d")
                    keyboard = get_renewal_keyboard_3d(language)
                    audit_message = "Paid subscription reminder (3d before expiry)"

                elif reminder_type == ReminderType.REMINDER_1D:
                    notif_key = "subscription.reminder_1d"
                    text = (await get_notification_text(notif_key, language=language)) or i18n.get_text(language, "reminder.paid_1d")
                    keyboard = get_renewal_keyboard_1d(language)
                    audit_message = "Paid subscription reminder (1d before expiry)"

                # Ветки REMINDER_24H здесь нет намеренно: окно «24 часа до
                # истечения» полностью занято REMINDER_1D (см.
                # app/services/notifications/service.py:should_send_reminder —
                # 24h с допуском 1 час). should_send_reminder не возвращает
                # REMINDER_24H ни в одном случае, поэтому ветка была
                # недостижима, а тумблер subscription.reminder_24h в дашборде
                # управлял текстом, который никогда не отправляется.

                elif reminder_type == ReminderType.REMINDER_3H:
                    notif_key = "subscription.reminder_3h"
                    text = (await get_notification_text(notif_key, language=language)) or i18n.get_text(language, "reminder.paid_3h_special")
                    keyboard = get_renewal_discount_keyboard(language)
                    audit_message = "Paid subscription reminder (3h before expiry) with 15% discount"

                # Если админ выключил через дашборд — мгновенный skip.
                # Помечаем reminder_sent, чтобы next-cycle тоже не спамил.
                if notif_key and not await is_notification_enabled(notif_key):
                    try:
                        await notification_service.mark_reminder_sent(telegram_id, reminder_type)
                        await log_notification_send(
                            notif_key, telegram_id, status="skipped_disabled",
                        )
                    except Exception as mark_err:
                        # Под pass уходила не только метрика, но и сама пометка
                        # reminder_sent — та, ради которой этот блок и написан
                        # («чтобы next-cycle тоже не спамил»). Если пометка не
                        # встала, напоминание уйдёт повторно на следующем витке,
                        # и узнать об этом было неоткуда.
                        logger.warning(
                            "REMINDER_SKIP_MARK_FAILED user=%s key=%s type=%s err=%s — "
                            "пометка могла не встать, возможен повтор на следующем витке",
                            telegram_id, notif_key, reminder_type, mark_err,
                        )
                    logger.info(
                        "reminder_skipped_disabled: user=%s key=%s",
                        telegram_id, notif_key,
                    )
                    continue

                # Опциональный segment_filter из trigger_config: если
                # админ задал сегмент, шлём только тем, кто в него входит.
                # Пример: reminder_7d + segment_filter='paid_expires_in_7d'
                # → отправится только тем, у кого сейчас платная активна.
                if notif_key:
                    from app.services.automated_notifications import is_user_in_segment
                    # Конфиг уже прочитан один раз выше — второй поход в базу
                    # на каждого пользователя не нужен.
                    _tcfg = trigger_configs.get(notif_key) or {}
                    _seg = str(_tcfg.get("segment_filter") or "").strip()
                    if _seg and not await is_user_in_segment(telegram_id, _seg):
                        # Пропускаем на этом цикле — юзер может войти в сегмент
                        # позже (изменится состояние). НЕ помечаем reminder_sent,
                        # чтобы дать шанс сработать после апдейта.
                        try:
                            await log_notification_send(
                                notif_key, telegram_id, status="skipped_disabled",
                            )
                        except Exception:
                            pass
                        logger.info(
                            "reminder_skipped_segment: user=%s key=%s seg=%s",
                            telegram_id, notif_key, _seg,
                        )
                        continue
                
                if text and keyboard:
                    # 3-day reminder gets a photo header on prod; everything else
                    # stays plain text.  Photo send falls back to text on any
                    # error (stale file_id, blocked, etc.) via safe_send_message.
                    sent = None
                    if reminder_type == ReminderType.REMINDER_3D:
                        photo_id = _REMINDER_3D_PHOTO.get(
                            "prod" if config.IS_PROD else "stage", ""
                        )
                        if photo_id:
                            try:
                                sent = await bot.send_photo(
                                    chat_id=telegram_id,
                                    photo=photo_id,
                                    caption=text,
                                    reply_markup=keyboard,
                                    parse_mode="HTML",
                                )
                            except Exception as e:
                                logger.warning(
                                    "REMINDER_3D photo send failed user=%s err=%s — "
                                    "falling back to plain text",
                                    telegram_id, type(e).__name__,
                                )
                                sent = None
                    if sent is None:
                        # Default path / photo fallback: plain text reminder.
                        sent = await safe_send_message(bot, telegram_id, text, reply_markup=keyboard)
                    if sent is None:
                        # Юзер заблокировал бота / чат недоступен.
                        if notif_key:
                            try:
                                await log_notification_send(
                                    notif_key, telegram_id, status="blocked",
                                )
                            except Exception:
                                pass
                        continue
                    sent_count += 1
                    await asyncio.sleep(0.05)  # Telegram rate limit: max 20 msgs/sec

                    # Mark reminder as sent using notification service
                    await notification_service.mark_reminder_sent(telegram_id, reminder_type)

                    # Метрика для admin-stats (только для reminder-типов
                    # обёрнутых в registry — admin/legacy остаются без).
                    if notif_key:
                        try:
                            await log_notification_send(
                                notif_key, telegram_id, status="sent",
                            )
                        except Exception:
                            pass

                    # Log to audit_log
                    await database._log_audit_event_atomic_standalone(
                        "reminder_sent",
                        telegram_id,
                        telegram_id,
                        audit_message
                    )

                    logger.info("Reminder (%s) sent to user %s", reminder_type.value, telegram_id)
                
            except Exception as e:
                # Ошибка для одного пользователя не должна ломать цикл
                logger.error("Error sending reminder to user %s: %s", telegram_id, e, exc_info=True)
                continue
                
    except Exception as e:
        logger.exception(f"Error in send_smart_reminders: {e}")

    return sent_count


async def reminders_task(bot: Bot):
    """Фоновая задача для отправки напоминаний об окончании подписки (выполняется каждые 30-60 минут)"""
    # Здесь был фиксированный asyncio.sleep(60).
    #
    # Из-за него reminders просыпался ровно в ту же секунду, что и остальные
    # воркеры с фиксированной паузой: разом шли выборки из базы и рассылка в
    # Telegram, причём ровно на прогреве бота. Случайная задержка (общее
    # правило, см. app/core/worker_startup) разносит старты по минутному окну
    # и разрывает кратность периодов, из-за которой фазы совпадали снова.
    await startup_jitter("reminders")

    iteration_number = 0
    while True:
        # Аварийный рубильник фоновых воркеров. Проверяем внутри цикла:
        # флаг читается из окружения и может смениться без рестарта.
        if background_workers_paused("reminders"):
            await asyncio.sleep(60)
            continue
        iteration_number += 1
        iteration_start_time = time.time()
        
        # H1+H2 fix: Add iteration start logging and timeout wrapper
        correlation_id = log_worker_iteration_start(
            worker_name="reminders",
            iteration_number=iteration_number
        )
        
        iteration_outcome = "success"
        iteration_error_type = None
        items_sent = 0

        try:
            # Жёсткий таймаут — только страховка от зависшего await. Штатный
            # выход по времени делает мягкий бюджет внутри send_smart_reminders,
            # он считается от размера выборки и заведомо меньше этого числа.
            try:
                items_sent = await asyncio.wait_for(
                    send_smart_reminders(bot),
                    timeout=REMINDERS_ITERATION_HARD_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "WORKER_TIMEOUT worker=reminders exceeded %.0fs — iteration cancelled",
                    REMINDERS_ITERATION_HARD_TIMEOUT,
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
            duration_ms = int((time.time() - iteration_start_time) * 1000)
            log_worker_iteration_end(
                worker_name="reminders",
                outcome=iteration_outcome,
                items_processed=items_sent,
                error_type=iteration_error_type,
                duration_ms=duration_ms,
            )

        # Здесь стоял `if iteration_outcome == "cancelled": break`. Ветка
        # недостижима: отмена выше делает raise, до этой строки исполнение уже
        # не доходит. Оставлять её вредно — она создаёт впечатление, что после
        # отмены цикл ещё что-то доделывает.

        # Проверяем каждые 45 минут для баланса между точностью и нагрузкой
        await asyncio.sleep(45 * 60)  # 45 минут в секундах
