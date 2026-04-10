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
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        return
    # Обработчик команды /start
    telegram_id = message.from_user.id
    # Single DB fetch — extract language directly (avoid duplicate get_user call)
    user = await database.get_user(telegram_id)
    is_new_user = user is None
    start_language = (user.get("language") or "ru") if user else "ru"
    # Safe username resolution: username or first_name or localized fallback
    username = safe_resolve_username(message.from_user, start_language, telegram_id)
    # Ограничиваем длину для БД
    if username and len(username) > 64:
        username = username[:64]

    # Создаем пользователя если его нет (user already fetched above)
    if not user:
        await database.create_user(telegram_id, username, start_language)
    else:
        # Update username + ensure referral_code in a single connection
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            if username is not None:
                await conn.execute(
                    "UPDATE users SET username = $1 WHERE telegram_id = $2",
                    username, telegram_id
                )
            if not user.get("referral_code"):
                referral_code = database.generate_referral_code(telegram_id)
                await conn.execute(
                    "UPDATE users SET referral_code = $1 WHERE telegram_id = $2 AND referral_code IS NULL",
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

                        # Fire-and-forget: create Remnawave bypass for gift recipient
                        try:
                            from app.services.remnawave_service import renew_remnawave_user_bg
                            if tariff in ("basic", "plus"):
                                sub = await database.get_subscription(telegram_id)
                                if sub and sub.get("expires_at"):
                                    renew_remnawave_user_bg(telegram_id, tariff, sub["expires_at"])
                        except Exception as rmn_err:
                            logger.warning("REMNAWAVE_GIFT_FAIL: tg=%s %s", telegram_id, rmn_err)

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
