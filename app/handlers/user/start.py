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
    # SECURITY: Бот работает ТОЛЬКО в личных сообщениях
    if message.chat.type != "private":
        return

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
    """Обработчик команды /start"""
    telegram_id = message.from_user.id
    # Safe username resolution: username or first_name or localized fallback
    user = await database.get_user(telegram_id)
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
    
    # 1. REFERRAL REGISTRATION: Process on FIRST interaction
    # This uses the new deterministic referral service
    referral_result = await process_referral_on_first_interaction(message, telegram_id)
    
    # Send notification to referrer if just registered
    if referral_result and referral_result.get("should_notify"):
        try:
            referrer_id = referral_result.get("referrer_id")
            if referrer_id:
                # Get referrer info
                referrer_user = await database.get_user(referrer_id)
                referrer_username = referrer_user.get("username") if referrer_user else None
                referrer_language = await resolve_user_language(referrer_id)
                
                # Get referred user info (safe: username or first_name or fallback)
                referred_username = username  # Already resolved via safe_resolve_username
                # Format display name: add @ prefix if username exists and doesn't have it
                user_fallback_text = i18n_get_text(referrer_language, "common.user")
                if referred_username and not referred_username.startswith("ID:") and referred_username != user_fallback_text:
                    referred_display = f"@{referred_username}" if not referred_username.startswith("@") else referred_username
                else:
                    referred_display = referred_username
                
                first_payment_msg = i18n_get_text(referrer_language, "referral.first_payment_notification")
                title = i18n_get_text(referrer_language, "referral.registered_title")
                user_line = i18n_get_text(referrer_language, "referral.registered_user", user=referred_display)
                date_line = i18n_get_text(referrer_language, "referral.registered_date", date=datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M'))
                notification_text = f"{title}\n\n{user_line}\n{date_line}\n\n{first_payment_msg}"
                
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
                    "referrer": referrer_id,
                    "referred": telegram_id,
                    "error": str(e)
                }
            )
    
    # Phase 4: ALWAYS show language selection first (pre-language-binding screen)
    text = i18n_get_text("ru", "lang.select_title")
    await message.answer(text, reply_markup=get_language_keyboard("ru"))
