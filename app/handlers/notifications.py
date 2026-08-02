"""
Notification sending helpers.

ONLY sending logic, no decision-making.
If a function both DECIDES and SENDS:
- decision stays in original module
- sending logic extracted here
"""
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import Optional
from urllib.parse import quote
import logging
import config
import database
from app.utils.referral_link import build_referral_link

logger = logging.getLogger(__name__)

_CASHBACK_PHOTO = {
    "prod": "AgACAgQAAxkBAAFU1CRqGqoAAS1KI0850DLxvYHcz47qlDQAAsYPaxtaLNFQ8CwBGB-Q9mUBAAMCAAN5AAM7BA",
    "stage": "AgACAgQAAxkBAAIfoGnenb15W2hobSqm_sQru9uQUjUjAAKnDGsbiHD4Ujk8OCW6NZ8hAQADAgADeQADOwQ",
}


async def send_referral_cashback_notification(
    bot: Bot,
    referrer_id: int,
    referred_id: int,
    purchase_amount: float,
    cashback_amount: float,
    cashback_percent: int,
    paid_referrals_count: int,
    referrals_needed: int,
    action_type: str = "purchase",
    subscription_period: Optional[str] = None
) -> bool:
    """
    F) NOTIFICATIONS: Unified referral notification helper.

    Отправить уведомление рефереру о начислении кешбэка.
    Использует единый сервис для форматирования текста.

    Args:
        bot: Экземпляр бота
        referrer_id: Telegram ID реферера
        referred_id: Telegram ID реферала
        purchase_amount: Сумма покупки в рублях
        cashback_amount: Сумма кешбэка в рублях
        cashback_percent: Процент кешбэка
        paid_referrals_count: Количество оплативших рефералов
        referrals_needed: Сколько рефералов нужно до следующего уровня
        action_type: Тип действия ("покупку", "продление", "пополнение")
        subscription_period: Период подписки (опционально, например "1 месяц")

    Returns:
        True если уведомление отправлено, False если ошибка
    """
    try:
        from app.services.language_service import resolve_user_language
        referrer_language = await resolve_user_language(referrer_id)

        from app.services.notifications.service import format_referral_notification_text

        notification_text = format_referral_notification_text(
            purchase_amount=purchase_amount,
            cashback_amount=cashback_amount,
            cashback_percent=cashback_percent,
            paid_referrals_count=paid_referrals_count,
            referrals_needed=referrals_needed,
            action_type=action_type,
            subscription_period=subscription_period,
            language=referrer_language
        )

        # Build share button with referrer's referral link
        from app.i18n import get_text as i18n_get_text
        bot_info = await bot.get_me()
        referral_link = await build_referral_link(referrer_id, bot_info.username)
        share_url = f"https://t.me/share/url?url={quote(referral_link)}"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(referrer_language, "referral.cashback_invite_button"),
                url=share_url
            )]
        ])

        photo_id = _CASHBACK_PHOTO.get("prod" if config.IS_PROD else "stage", "")
        if photo_id:
            await bot.send_photo(
                chat_id=referrer_id,
                photo=photo_id,
                caption=notification_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        else:
            await bot.send_message(
                chat_id=referrer_id,
                text=notification_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )

        logger.info(
            f"REFERRAL_NOTIFICATION_SENT [referrer={referrer_id}, "
            f"referred={referred_id}, amount={cashback_amount:.2f} RUB, percent={cashback_percent}%, "
            f"action={action_type}]"
        )

        return True

    except Exception as e:
        logger.warning(
            "NOTIFICATION_FAILED",
            extra={
                "type": "referral_cashback",
                "referrer": referrer_id,
                "referred": referred_id,
                "amount": purchase_amount,
                "cashback": cashback_amount,
                "error": str(e)
            }
        )
        return False


def format_subscription_period(period_days: Optional[int]) -> Optional[str]:
    """Человеческая длительность подписки для текста уведомления.

    30 → «1 месяц», 90 → «3 месяца», 365 → «12 месяцев». Некратные периоды
    (например, 45 дней после бонуса) отдаём как есть в днях, потому что
    «1 месяц» для 45 дней — это враньё в тексте, который видит реферер.
    """
    if not period_days:
        return None
    known = {30: "1 месяц", 90: "3 месяца", 180: "6 месяцев", 365: "12 месяцев"}
    if period_days in known:
        return known[period_days]
    months, rest = divmod(period_days, 30)
    if months and not rest:
        # 2-4 → «месяца», 5+ → «месяцев», 1 попадает в known выше.
        suffix = "а" if months in (2, 3, 4) else "ев"
        return f"{months} месяц{suffix}"
    return f"{period_days} дней"


async def notify_referral_cashback(
    bot: Bot,
    referral_reward: Optional[dict],
    *,
    referred_id: int,
    purchase_amount: float,
    action_type: str = "purchase",
    period_days: Optional[int] = None,
    context: str = "",
) -> bool:
    """Уведомить реферера о начисленном кешбэке — единая точка входа.

    Зачем нужна: кешбэк начисляет finalize_purchase внутри транзакции и
    возвращает словарь referral_reward. Уведомление же отправляет вызывающий
    код, и таких мест несколько — оплата картой в Telegram, оплата с баланса,
    вебхуки CryptoBot/Lava/Платеги. Вебхуки этот словарь просто не читали:
    деньги рефереру начислялись, а сообщения он не получал никогда.

    Функция сама разбирает словарь и молча выходит, если кешбэка не было
    (`referral_reward` пустой или success=False) — вызывающему коду не нужно
    повторять эту проверку. Исключения не пробрасываются: платёж уже
    закоммичен, и падение уведомления не должно его ронять.

    Args:
        referral_reward: результат process_referral_reward из finalize_purchase.
        referred_id: кто заплатил (реферал).
        purchase_amount: сумма покупки в рублях.
        action_type: "purchase" | "topup" — влияет на текст.
        period_days: срок подписки, чтобы подставить «на 3 месяца».
        context: метка для логов (например, имя провайдера).

    Returns: True — уведомление отправлено, False — кешбэка не было или сбой.
    """
    if not referral_reward or not referral_reward.get("success"):
        return False
    try:
        sent = await send_referral_cashback_notification(
            bot=bot,
            referrer_id=referral_reward.get("referrer_id"),
            referred_id=referred_id,
            purchase_amount=purchase_amount,
            cashback_amount=referral_reward.get("reward_amount"),
            cashback_percent=referral_reward.get("percent"),
            paid_referrals_count=referral_reward.get("paid_referrals_count", 0),
            referrals_needed=referral_reward.get("referrals_needed", 0),
            action_type=action_type,
            subscription_period=format_subscription_period(period_days),
        )
        if not sent:
            logger.warning(
                "REFERRAL_NOTIFICATION_FAILED [context=%s, referrer=%s, referred=%s]",
                context, referral_reward.get("referrer_id"), referred_id,
            )
        return sent
    except Exception as e:
        logger.exception(
            "REFERRAL_NOTIFICATION_ERROR [context=%s, referred=%s]: %s",
            context, referred_id, e,
        )
        return False
