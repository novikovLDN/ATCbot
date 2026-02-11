"""
Notification sending helpers.

ONLY sending logic, no decision-making.
If a function both DECIDES and SENDS:
- decision stays in original module
- sending logic extracted here
"""
from aiogram import Bot
from typing import Optional
import logging
import database

logger = logging.getLogger(__name__)


async def send_referral_cashback_notification(
    bot: Bot,
    referrer_id: int,
    referred_id: int,
    purchase_amount: float,
    cashback_amount: float,
    cashback_percent: int,
    paid_referrals_count: int,
    referrals_needed: int,
    action_type: str = "покупку",
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
        # Получаем язык реферера для локализации уведомления
        referrer_user = await database.get_user(referrer_id)
        referrer_language = referrer_user.get("language", "ru") if referrer_user else "ru"
        
        # Получаем информацию о реферале (username or first_name or localized fallback)
        referred_user = await database.get_user(referred_id)
        # Import helper function at runtime to avoid circular dependency
        import handlers
        referred_username = handlers.safe_resolve_username_from_db(referred_user, referrer_language, referred_id)
        
        # Локализуем action_type
        import localization
        if action_type == "покупка" or action_type == "покупку":
            localized_action_type = localization.get_text(referrer_language, "action_purchase", default="покупку")
        elif action_type == "продление":
            localized_action_type = localization.get_text(referrer_language, "action_renewal", default="продление")
        elif action_type == "пополнение":
            localized_action_type = localization.get_text(referrer_language, "action_topup", default="пополнение")
        else:
            localized_action_type = action_type
        
        # Используем единый сервис для форматирования
        from app.services.notifications.service import format_referral_notification_text
        
        notification_text = format_referral_notification_text(
            referred_username=referred_username,
            referred_id=referred_id,
            purchase_amount=purchase_amount,
            cashback_amount=cashback_amount,
            cashback_percent=cashback_percent,
            paid_referrals_count=paid_referrals_count,
            referrals_needed=referrals_needed,
            action_type=localized_action_type,
            subscription_period=subscription_period,
            language=referrer_language
        )
        
        # Отправляем уведомление
        await bot.send_message(
            chat_id=referrer_id,
            text=notification_text
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
