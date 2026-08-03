"""
Critical admin alert service.

Sends alerts to ADMIN_TELEGRAM_ID for events that require immediate attention:
- Payment processing failures
- Subscription activation failures
- Worker crashes / prolonged failures
- Database connectivity issues
- VPN API failures affecting users

Rate-limited per category to prevent alert storms.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import config

logger = logging.getLogger(__name__)

# Per-category cooldowns to prevent alert spam (seconds)
_ALERT_COOLDOWNS = {
    "payment": 60,        # 1 min — every payment failure is important
    "subscription": 120,  # 2 min
    "worker": 300,        # 5 min
    "database": 600,      # 10 min
    "vpn_api": 300,       # 5 min
    "security": 0,        # no cooldown — always alert
}

# Last alert timestamp per category
_last_alert_at: dict[str, float] = {}

# Сколько алертов категории проглотил кулдаун с момента последней отправки.
#
# Зачем считать. Кулдаун защищает от шторма, но раньше он же прятал его
# масштаб: при аварии первый алерт уходил, а следующие сто просто возвращали
# False. Админ видел одну строчку и думал, что сломался один платёж. Счётчик
# едет в следующее сообщение — «и ещё N таких же» — и авария сразу отличима
# от единичного сбоя.
_suppressed_since_last: dict[str, int] = {}

# Пауза перед повторной попыткой отправки. Вынесена в константу, чтобы тесты
# не ждали две секунды на каждый прогон.
_RETRY_DELAY_SECONDS = 2.0


async def send_alert(
    bot,
    category: str,
    message: str,
    *,
    force: bool = False,
) -> bool:
    """Send critical alert to admin.

    Args:
        bot: aiogram Bot instance
        category: Alert category (payment, subscription, worker, database, vpn_api, security)
        message: Alert text (will be prefixed with category header)
        force: Bypass cooldown (for truly critical one-off events)

    Returns:
        True if alert was sent, False if rate-limited or failed

    Почему функция не имеет права упасть. Это последний рубеж: её зовут из
    except-веток обработки платежей и из воркеров. Исключение отсюда гасит
    вызывающий код или, хуже, подменяет собой исходную ошибку в логе. Поэтому
    здесь всё завёрнуто, а наружу идёт только bool.
    """
    now = time.monotonic()
    cooldown = _ALERT_COOLDOWNS.get(category, 300)

    if not force:
        last = _last_alert_at.get(category, 0.0)
        if now - last < cooldown:
            suppressed = _suppressed_since_last.get(category, 0) + 1
            _suppressed_since_last[category] = suppressed
            logger.warning(
                "ADMIN_ALERT_SUPPRESSED category=%s suppressed_since_last=%d cooldown=%ds",
                category, suppressed, cooldown,
            )
            return False

    if bot is None:
        # Отдельная ветка, потому что без неё мы бы ушли в общий except,
        # проспали две секунды и получили тот же AttributeError на повторе.
        logger.error("ADMIN_ALERT_NO_BOT category=%s — алерт потерян", category)
        return False

    # Текст собираем ДО try.
    #
    # Раньше full_message создавался внутри try. Если падало само
    # формирование (например, category оказывалась не строкой и .upper()
    # бросал AttributeError), то в ветке повтора имя full_message не
    # существовало — второй вызов падал с NameError, и в логе оставалось
    # ADMIN_ALERT_RETRY_FAILED с причиной, не имеющей отношения к делу.
    # Настоящая ошибка при этом терялась.
    header = _CATEGORY_HEADERS.get(category, f"[{category}]".upper())
    suppressed = _suppressed_since_last.get(category, 0)
    body = message if isinstance(message, str) else str(message)
    if suppressed:
        body += (
            f"\n\n(и ещё {suppressed} таких же алертов за последние "
            f"{cooldown} с — показан только этот)"
        )
    full_message = f"{header}\n{body}"

    # Truncate to Telegram message limit
    if len(full_message) > 4000:
        full_message = full_message[:3997] + "..."

    # Две попытки одним циклом: тело отправки общее, значит расходиться им
    # некуда. Прошлая версия дублировала вызов руками — так и разъехалось.
    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            await asyncio.wait_for(
                bot.send_message(config.ADMIN_TELEGRAM_ID, full_message),
                timeout=10.0,
            )
            # Счётчик обнуляем только после подтверждённой доставки: если
            # сбросить раньше, при неудаче мы потеряем масштаб аварии.
            _suppressed_since_last.pop(category, None)
            _last_alert_at[category] = time.monotonic()
            if attempt == 1:
                logger.info(f"ADMIN_ALERT_SENT category={category}")
            else:
                logger.info(f"ADMIN_ALERT_SENT_RETRY category={category}")
            return True
        except Exception as e:
            last_error = e
            if attempt == 1:
                logger.error(f"ADMIN_ALERT_FAILED category={category} error={e}")
                try:
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)
                except asyncio.CancelledError:
                    # Воркер гасят — не мешаем остановке, но и не притворяемся,
                    # что алерт доставлен.
                    raise

    logger.error(f"ADMIN_ALERT_RETRY_FAILED category={category} error={last_error}")
    # Недоставленный алерт тоже подавленный: он должен попасть в счётчик
    # следующего, иначе авария на N событий отразится числом N-1.
    _suppressed_since_last[category] = suppressed + 1
    return False


_CATEGORY_HEADERS = {
    "payment": "PAYMENT ALERT",
    "subscription": "SUBSCRIPTION ALERT",
    "worker": "WORKER ALERT",
    "database": "DATABASE ALERT",
    "vpn_api": "VPN API ALERT",
    "security": "SECURITY ALERT",
}


# Convenience functions for common alert scenarios

async def alert_payment_failure(
    bot,
    provider: str,
    telegram_id: int,
    purchase_id: str,
    error: Exception,
    is_transient: bool = False,
    *,
    amount_rubles: Optional[float] = None,
    tariff: Optional[str] = None,
    period_days: Optional[int] = None,
) -> bool:
    """Alert admin about payment processing failure."""
    severity = "TRANSIENT" if is_transient else "PERMANENT"
    retry = "Provider will retry." if is_transient else "NEEDS MANUAL CHECK!"
    now_str = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    details = (
        f"[{severity}]\n"
        f"Date: {now_str}\n"
        f"Provider: {provider}\n"
        f"User TG ID: {telegram_id}\n"
        f"Purchase: {purchase_id}\n"
    )
    if amount_rubles is not None:
        details += f"Amount: {amount_rubles} RUB\n"
    if tariff:
        details += f"Tariff: {tariff}\n"
    if period_days is not None:
        details += f"Period: {period_days} days\n"
    details += (
        f"Error: {type(error).__name__}: {str(error)[:200]}\n"
        f"{retry}"
    )
    return await send_alert(
        bot,
        "payment",
        details,
        force=not is_transient,  # permanent failures always alert
    )


async def alert_subscription_failure(
    bot,
    telegram_id: int,
    action: str,
    error: Exception,
    *,
    amount_rubles: Optional[float] = None,
    tariff: Optional[str] = None,
    period_days: Optional[int] = None,
    subscription_id: Optional[int] = None,
) -> bool:
    """Alert admin about subscription activation/renewal failure."""
    now_str = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    details = (
        f"Action: {action}\n"
        f"Date: {now_str}\n"
        f"User TG ID: {telegram_id}\n"
    )
    if subscription_id is not None:
        details += f"Subscription ID: {subscription_id}\n"
    if amount_rubles is not None:
        details += f"Amount: {amount_rubles} RUB\n"
    if tariff:
        details += f"Tariff: {tariff}\n"
    if period_days is not None:
        details += f"Period: {period_days} days\n"
    details += f"Error: {type(error).__name__}: {str(error)[:200]}"
    return await send_alert(
        bot,
        "subscription",
        details,
    )


async def alert_worker_failure(
    bot,
    worker_name: str,
    error: Exception,
    iteration: Optional[int] = None,
) -> bool:
    """Alert admin about background worker failure."""
    iter_str = f"\nIteration: {iteration}" if iteration is not None else ""
    return await send_alert(
        bot,
        "worker",
        f"Worker: {worker_name}{iter_str}\n"
        f"Error: {type(error).__name__}: {str(error)[:200]}",
    )


async def alert_vpn_api_failure(
    bot,
    operation: str,
    telegram_id: int,
    error: Exception,
    *,
    amount_rubles: Optional[float] = None,
    tariff: Optional[str] = None,
    period_days: Optional[int] = None,
    subscription_id: Optional[int] = None,
) -> bool:
    """Alert admin about VPN API failure affecting a user."""
    now_str = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    details = (
        f"Operation: {operation}\n"
        f"Date: {now_str}\n"
        f"User TG ID: {telegram_id}\n"
    )
    if subscription_id is not None:
        details += f"Subscription ID: {subscription_id}\n"
    if amount_rubles is not None:
        details += f"Amount: {amount_rubles} RUB\n"
    if tariff:
        details += f"Tariff: {tariff}\n"
    if period_days is not None:
        details += f"Period: {period_days} days\n"
    details += f"Error: {type(error).__name__}: {str(error)[:200]}"
    return await send_alert(
        bot,
        "vpn_api",
        details,
    )


async def alert_security_event(
    bot,
    event: str,
    details: str,
) -> bool:
    """Alert admin about security event (always sends, no cooldown)."""
    return await send_alert(
        bot,
        "security",
        f"Event: {event}\n{details}",
        force=True,
    )
