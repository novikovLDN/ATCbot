"""
User command: /start
"""
import logging
from datetime import datetime, timezone

import database
import config
from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.utils.referral_middleware import process_referral_on_first_interaction
from app.handlers.common.guards import ensure_db_ready_message
from app.handlers.common.keyboards import get_language_keyboard, get_main_menu_keyboard
from app.handlers.common.utils import safe_resolve_username

user_router = Router()
logger = logging.getLogger(__name__)


@user_router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    # SECURITY: Только private chat
    if message.chat.type != "private":
        return

    # SECURITY: Проверка что /start не содержит подозрительный payload
    # Допускаем только /start и /start <referral_code> (alphanumeric, max 64 символов)
    if message.text:
        text = message.text.strip()
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            payload = parts[1]
            if len(payload) > 64 or not payload.replace("_", "").replace(
                "-", ""
            ).isalnum():
                logger.warning(
                    "INVALID_START_PAYLOAD user=%s payload=%s",
                    message.from_user.id,
                    payload[:30],
                )
                pass  # Невалидный payload — обрабатываем как обычный /start без реферала

    await state.clear()
    # SAFE STARTUP GUARD: Проверка готовности БД
    # /start может работать в деградированном режиме (только показ меню),
    # но если БД недоступна, не пытаемся создавать пользователя
    if not database.DB_READY:
        # В STAGE показываем меню без сообщения об ошибке (read-only режим)
        # В PROD показываем сообщение об ошибке
        language = await resolve_user_language(message.from_user.id)
        text = i18n_get_text(language, "main.welcome")
        if config.IS_PROD:
            text += "\n\n" + i18n_get_text(language, "main.service_unavailable")
        keyboard = await get_main_menu_keyboard(language, message.from_user.id)
        await message.answer(text, reply_markup=keyboard)
        return
    # Обработчик команды /start
    telegram_id = message.from_user.id
    # Safe username resolution: username or first_name or localized fallback
    user = await database.get_user(telegram_id)
    is_new_user = user is None
    start_language = await resolve_user_language(telegram_id)
    username = safe_resolve_username(message.from_user, start_language, telegram_id)
    # Ограничиваем длину для БД
    if username and len(username) > 64:
        username = username[:64]

    # Создаем пользователя если его нет (user already fetched above)
    if not user:
        await database.create_user(telegram_id, username, start_language)
    else:
        # Обновляем username если изменился (safe: username can be None)
        if username is not None:
            await database.update_username(telegram_id, username)
        # Убеждаемся, что у пользователя есть referral_code
        if not user.get("referral_code"):
            # Генерируем код для существующего пользователя
            referral_code = database.generate_referral_code(telegram_id)
            pool = await database.get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE users SET referral_code = $1 WHERE telegram_id = $2",
                    referral_code, telegram_id
                )
    
    # GIFT ACTIVATION: Обработка подарочной ссылки /start gift_XXXXX
    if message.text:
        start_parts = message.text.strip().split(maxsplit=1)
        if len(start_parts) > 1 and start_parts[1].startswith("gift_"):
            gift_code = start_parts[1][5:]  # Убираем "gift_" префикс
            if gift_code and len(gift_code) <= 20 and gift_code.isalnum():
                try:
                    activation_result = await database.activate_gift_subscription(
                        gift_code=gift_code,
                        activated_by=telegram_id,
                    )
                    language = await resolve_user_language(telegram_id)

                    if activation_result["success"]:
                        tariff = activation_result["tariff"]
                        period_days = activation_result["period_days"]
                        tariff_name = "Basic" if tariff == "basic" else "Plus"
                        months = period_days // 30
                        if months == 1:
                            period_text = "1 месяц"
                        elif months in (2, 3, 4):
                            period_text = f"{months} месяца"
                        else:
                            period_text = f"{months} месяцев"

                        if is_new_user:
                            # Новый пользователь: приветствие + активация + выбор языка
                            text = i18n_get_text(
                                language, "gift.activated_welcome",
                                tariff_name=tariff_name,
                                period=period_text,
                            )
                            await message.answer(
                                text,
                                reply_markup=get_language_keyboard(language),
                                parse_mode="HTML",
                            )
                        else:
                            # Существующий пользователь: активация + главное меню
                            text = i18n_get_text(
                                language, "gift.activated",
                                tariff_name=tariff_name,
                                period=period_text,
                            )
                            keyboard = await get_main_menu_keyboard(language, telegram_id)
                            await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
                        logger.info(f"GIFT_ACTIVATED_VIA_LINK user={telegram_id} code={gift_code} new_user={is_new_user}")
                        return
                    else:
                        error = activation_result.get("error", "unknown")
                        error_keys = {
                            "not_found": "gift.error_not_found",
                            "already_activated": "gift.error_already_activated",
                            "expired": "gift.error_expired",
                            "self_activation": "gift.error_self_activation",
                            "invalid_status": "gift.error_invalid",
                        }
                        error_key = error_keys.get(error, "gift.error_invalid")
                        text = i18n_get_text(language, error_key)
                        if is_new_user:
                            keyboard = get_language_keyboard(language)
                        else:
                            keyboard = await get_main_menu_keyboard(language, telegram_id)
                        await message.answer(text, reply_markup=keyboard)
                        logger.warning(f"GIFT_ACTIVATION_FAILED user={telegram_id} code={gift_code} error={error}")
                        return
                except Exception as e:
                    logger.exception(f"Gift activation error: user={telegram_id}, code={gift_code}, error={e}")
                    language = await resolve_user_language(telegram_id)
                    text = i18n_get_text(language, "gift.error_invalid")
                    if is_new_user:
                        keyboard = get_language_keyboard(language)
                    else:
                        keyboard = await get_main_menu_keyboard(language, telegram_id)
                    await message.answer(text, reply_markup=keyboard)
                    return

    # SITE LINK: /start TOKEN (16-char hex from site "Перейти в Telegram" button)
    # Token format: exactly 16 hex chars, no prefix (not gift_, not ref_)
    if message.text and config.SITE_INTEGRATION_ENABLED:
        start_parts = message.text.strip().split(maxsplit=1)
        if len(start_parts) > 1:
            site_token = start_parts[1]
            # Site token: 16 hex chars, no known prefix
            if (
                len(site_token) == 16
                and all(c in "0123456789abcdef" for c in site_token.lower())
                and not site_token.startswith(("gift_", "ref_"))
            ):
                try:
                    from app.services.site_client import get_user_by_token, link_telegram, extend_subscription

                    site_user = await get_user_by_token(site_token)
                    if site_user:
                        # Link Telegram to site account
                        link_result = await link_telegram(site_token, telegram_id)
                        email = site_user.get("email", "N/A")
                        logger.info(
                            "SITE_LINK_VIA_START user=%s token=%s...%s email=%s",
                            telegram_id, site_token[:4], site_token[-4:], email,
                        )

                        language = await resolve_user_language(telegram_id)
                        site_days = site_user.get("daysLeft", 0) or site_user.get("days", 0) or 0

                        # Sync bot subscription → site if bot has more days
                        bot_days = 0
                        try:
                            bot_sub = await database.get_subscription(telegram_id)
                            if bot_sub and bot_sub.get("expires_at"):
                                delta = bot_sub["expires_at"] - datetime.now(timezone.utc)
                                bot_days = max(0, delta.days)
                        except Exception as e:
                            logger.warning("SITE_SYNC_BOT_SUB_ERROR user=%s: %s", telegram_id, e)

                        if bot_days > site_days:
                            extra_days = bot_days - site_days
                            ok = await extend_subscription(telegram_id, extra_days)
                            if ok:
                                logger.info(
                                    "SITE_SYNC_EXTEND user=%s bot_days=%s site_days=%s extended_by=%s",
                                    telegram_id, bot_days, site_days, extra_days,
                                )
                                site_days = bot_days

                        days_left = site_days

                        if days_left > 0:
                            # Active subscription on site
                            await message.answer(
                                i18n_get_text(language, "site.linked_active",
                                    f"Telegram привязан к аккаунту {email}\n"
                                    f"Подписка: {days_left} дн.\n"
                                    f"Ваш VPN-ключ активен на сайте",
                                    email=email, days=days_left),
                                reply_markup=await get_main_menu_keyboard(language, telegram_id),
                                parse_mode="HTML",
                            )
                        else:
                            # No active subscription — offer payment
                            await message.answer(
                                i18n_get_text(language, "site.linked_expired",
                                    f"Telegram привязан к {email}\n"
                                    f"Подписка истекла. Оплатите для продления.",
                                    email=email),
                                reply_markup=await get_main_menu_keyboard(language, telegram_id),
                                parse_mode="HTML",
                            )
                        return
                    else:
                        logger.warning(
                            "SITE_TOKEN_NOT_FOUND user=%s token=%s...%s",
                            telegram_id, site_token[:4], site_token[-4:],
                        )
                except Exception as e:
                    # Site API failure must NOT break /start
                    logger.warning(
                        "SITE_LINK_ERROR user=%s error=%s: %s",
                        telegram_id, type(e).__name__, e,
                    )

    # SITE STATUS: Returning user without token — check site subscription status
    if not is_new_user and config.SITE_INTEGRATION_ENABLED:
        # Only check if no payload (pure /start) to avoid double-checking after token/gift/ref
        has_payload = message.text and len(message.text.strip().split(maxsplit=1)) > 1
        if not has_payload:
            try:
                from app.services.site_client import get_user_by_telegram
                site_user = await get_user_by_telegram(telegram_id)
                if site_user:
                    language = await resolve_user_language(telegram_id)
                    days_left = site_user.get("daysLeft", 0) or site_user.get("days", 0) or 0
                    email = site_user.get("email", "")
                    status_icon = "active" if days_left > 0 else "expired"
                    await message.answer(
                        i18n_get_text(language, f"site.status_{status_icon}",
                            f"С возвращением!\n"
                            f"Подписка: {days_left} дн.\n"
                            f"{'Активна' if days_left > 0 else 'Истекла'}",
                            days=days_left, email=email),
                        reply_markup=await get_main_menu_keyboard(language, telegram_id),
                        parse_mode="HTML",
                    )
                    return
            except Exception as e:
                logger.warning(
                    "SITE_STATUS_CHECK_ERROR user=%s error=%s: %s",
                    telegram_id, type(e).__name__, e,
                )

    # 1. REFERRAL REGISTRATION: Process ONLY for new users
    # Protects against: self-referral and existing users clicking referral links later
    referral_result = None
    if is_new_user:
        referral_result = await process_referral_on_first_interaction(message, telegram_id)
    else:
        # Existing user clicked a referral link — ignore and log
        if message.text:
            start_parts = message.text.strip().split(maxsplit=1)
            if len(start_parts) > 1 and start_parts[1].startswith("ref_"):
                logger.warning(
                    "REFERRAL_BLOCKED_EXISTING_USER user=%s payload=%s",
                    telegram_id, start_parts[1][:30]
                )
    
    # Send notification to referrer if just registered
    if referral_result and referral_result.get("should_notify"):
        try:
            referrer_id = referral_result.get("referrer_id")
            if referrer_id:
                referrer_language = await resolve_user_language(referrer_id)

                first_payment_msg = i18n_get_text(referrer_language, "referral.first_payment_notification")
                title = i18n_get_text(referrer_language, "referral.registered_title")
                date_line = i18n_get_text(referrer_language, "referral.registered_date", date=datetime.now(timezone.utc).strftime('%d.%m.%Y'))
                notification_text = f"{title}\n\n{date_line}\n\n{first_payment_msg}"
                
                await message.bot.send_message(
                    chat_id=referrer_id,
                    text=notification_text
                )
                
                logger.info(
                    f"REFERRAL_NOTIFICATION_SENT [type=registration, referrer={referrer_id}, "
                    f"referred={telegram_id}]"
                )
        except Exception as e:
            # Non-critical - log but don't fail
            logger.warning(
                "NOTIFICATION_FAILED",
                extra={
                    "type": "referral_registration",
                    "referrer": referral_result.get("referrer_id"),
                    "referred": telegram_id,
                    "error": str(e)
                }
            )
    
    # Phase 4: ALWAYS show language selection first (pre-language-binding screen)
    text = i18n_get_text(start_language, "lang.select_title")
    await message.answer(text, reply_markup=get_language_keyboard(start_language))
