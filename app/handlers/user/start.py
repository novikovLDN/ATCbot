"""
User command: /start

Handles:
- Regular /start (new user registration + site sync)
- /start ref_XXX (referral registration)
- /start gift_XXX (gift activation)
- /start tglogin_NONCE (Telegram web login via site deep link)
- /start LINK_TOKEN (link existing site account to Telegram)
- /start weblogin (generate nonce for web login)
"""
import logging
import uuid as uuid_module
from datetime import datetime, timezone

import database
import config
from aiogram import Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
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


def _extract_payload(message: Message) -> str | None:
    """Extract and validate the deep link payload from /start command."""
    if not message.text:
        return None
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None
    payload = parts[1]
    if len(payload) > 64 or not payload.replace("_", "").replace("-", "").isalnum():
        logger.warning(
            "INVALID_START_PAYLOAD user=%s payload=%s",
            message.from_user.id,
            payload[:30],
        )
        return None
    return payload


async def _handle_tglogin(message: Message, telegram_id: int, nonce: str) -> bool:
    """
    Handle /start tglogin_{nonce} — Telegram web login flow.

    1. Call POST /api/bot/auth-login with telegramId + nonce
    2. If NOT_LINKED (404) — auto-register via /api/bot/register, then retry auth-login
    3. Send confirmation to user
    """
    if not config.SITE_SYNC_ENABLED:
        logger.warning("TGLOGIN_SKIPPED: site sync disabled, user=%s", telegram_id)
        return False

    from app.services.site_api import auth_login, register_user, SiteApiNotFound, SiteApiError

    language = await resolve_user_language(telegram_id)

    try:
        await auth_login(telegram_id, nonce)
        await message.answer(i18n_get_text(language, "site.auth_success", "✅ Вы авторизованы на сайте!"))
        logger.info("TGLOGIN_SUCCESS user=%s nonce=%s", telegram_id, nonce[:8])
        return True
    except SiteApiNotFound as e:
        if e.code == "NOT_LINKED":
            # Auto-register on the site, then retry auth
            try:
                await register_user(telegram_id)
                await auth_login(telegram_id, nonce)
                await message.answer(i18n_get_text(language, "site.auth_success", "✅ Вы авторизованы на сайте!"))
                logger.info("TGLOGIN_AUTO_REGISTER_SUCCESS user=%s", telegram_id)
                return True
            except Exception as reg_err:
                logger.error("TGLOGIN_AUTO_REGISTER_FAILED user=%s error=%s", telegram_id, reg_err)
                await message.answer(i18n_get_text(language, "site.auth_error", "❌ Ошибка авторизации. Попробуйте позже."))
                return True
        else:
            logger.error("TGLOGIN_NOT_FOUND user=%s error=%s", telegram_id, e)
            await message.answer(i18n_get_text(language, "site.auth_error", "❌ Ошибка авторизации. Попробуйте позже."))
            return True
    except SiteApiError as e:
        logger.error("TGLOGIN_ERROR user=%s error=%s", telegram_id, e)
        await message.answer(i18n_get_text(language, "site.auth_error", "❌ Ошибка авторизации. Попробуйте позже."))
        return True


async def _handle_weblogin(message: Message, telegram_id: int) -> bool:
    """
    Handle /start weblogin — generate nonce and show link to site.
    """
    if not config.SITE_SYNC_ENABLED:
        return False

    language = await resolve_user_language(telegram_id)
    nonce = str(uuid_module.uuid4())
    site_url = f"{config.SITE_API_URL}/?tg_nonce={nonce}"

    from app.services.site_api import auth_login, register_user, SiteApiNotFound, SiteApiError

    try:
        await auth_login(telegram_id, nonce)
    except SiteApiNotFound as e:
        if e.code == "NOT_LINKED":
            try:
                await register_user(telegram_id)
                await auth_login(telegram_id, nonce)
            except Exception as reg_err:
                logger.error("WEBLOGIN_REGISTER_FAILED user=%s error=%s", telegram_id, reg_err)
                await message.answer(i18n_get_text(language, "site.auth_error", "❌ Ошибка авторизации. Попробуйте позже."))
                return True
    except SiteApiError as e:
        logger.error("WEBLOGIN_ERROR user=%s error=%s", telegram_id, e)
        await message.answer(i18n_get_text(language, "site.auth_error", "❌ Ошибка авторизации. Попробуйте позже."))
        return True

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "site.open_site", "🌐 Открыть сайт"),
            url=site_url,
        )]
    ])
    await message.answer(
        i18n_get_text(language, "site.weblogin_prompt", "Нажмите кнопку, чтобы войти на сайт:"),
        reply_markup=keyboard,
    )
    logger.info("WEBLOGIN_NONCE_GENERATED user=%s", telegram_id)
    return True


async def _handle_link_token(message: Message, telegram_id: int, token: str) -> bool:
    """
    Handle /start {linkToken} — link existing site account to Telegram.
    Token is a 16+ char hex string from the site.
    """
    if not config.SITE_SYNC_ENABLED:
        return False

    from app.services.site_api import link_telegram, SiteApiNotFound, SiteApiError

    language = await resolve_user_language(telegram_id)

    try:
        result = link_telegram(token, telegram_id)
        # link_telegram is async
        result = await result
        await message.answer(
            i18n_get_text(language, "site.link_success", "✅ Telegram привязан к вашему аккаунту на сайте!")
        )
        logger.info("SITE_LINK_SUCCESS user=%s", telegram_id)
        return True
    except SiteApiNotFound:
        logger.warning("SITE_LINK_TOKEN_NOT_FOUND user=%s token=%s", telegram_id, token[:8])
        await message.answer(
            i18n_get_text(language, "site.link_error", "❌ Ссылка недействительна или аккаунт уже привязан.")
        )
        return True
    except SiteApiError as e:
        logger.error("SITE_LINK_ERROR user=%s error=%s", telegram_id, e)
        await message.answer(
            i18n_get_text(language, "site.link_error", "❌ Ошибка привязки. Попробуйте позже.")
        )
        return True


async def _sync_register_on_site(telegram_id: int, referral_code: str | None = None) -> None:
    """
    Register user on the site (best-effort, non-blocking).
    Called after local user creation in the bot.
    """
    if not config.SITE_SYNC_ENABLED:
        return
    try:
        from app.services.site_api import register_user, sync_vpn_key_to_local
        site_data = await register_user(telegram_id, referral_code)
        # Sync vpnKey from site to local DB
        if site_data and site_data.get("vpnKey"):
            await sync_vpn_key_to_local(telegram_id, site_data["vpnKey"])
        logger.info("SITE_REGISTER_SYNC user=%s", telegram_id)
    except Exception as e:
        # Non-critical: site registration failure should not block bot flow
        logger.warning("SITE_REGISTER_SYNC_FAILED user=%s error=%s", telegram_id, e)


@user_router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    # SECURITY: Только private chat
    if message.chat.type != "private":
        return

    payload = _extract_payload(message)

    await state.clear()

    telegram_id = message.from_user.id

    # =====================================================================
    # SITE SYNC: Handle tglogin deep link (before DB check)
    # /start tglogin_{nonce} — Telegram web login flow
    # =====================================================================
    if payload and payload.startswith("tglogin_"):
        nonce = payload[8:]  # Remove "tglogin_" prefix
        if nonce:
            handled = await _handle_tglogin(message, telegram_id, nonce)
            if handled:
                return

    # /start weblogin — generate nonce and show site link
    if payload and payload == "weblogin":
        handled = await _handle_weblogin(message, telegram_id)
        if handled:
            return

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
    # Safe username resolution: username or first_name or localized fallback
    user = await database.get_user(telegram_id)
    is_new_user = user is None
    start_language = await resolve_user_language(telegram_id)
    username = safe_resolve_username(message.from_user, start_language, telegram_id)
    # Ограничиваем длину для БД
    if username and len(username) > 64:
        username = username[:64]

    # Extract referral code from payload for site sync
    ref_code_for_site = None
    if payload and payload.startswith("ref_"):
        ref_code_for_site = payload[4:]

    # Создаем пользователя если его нет (user already fetched above)
    if not user:
        await database.create_user(telegram_id, username, start_language)
        # SITE SYNC: Register on site (best-effort)
        await _sync_register_on_site(telegram_id, ref_code_for_site)
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

    # =====================================================================
    # SITE SYNC: Handle link token (non-ref, non-gift, non-tglogin payload)
    # This handles deep links from the site to link an existing account
    # =====================================================================
    if payload and not payload.startswith(("ref_", "gift_", "tglogin_")) and payload != "weblogin":
        handled = await _handle_link_token(message, telegram_id, payload)
        if handled:
            # After linking, show main menu
            keyboard = await get_main_menu_keyboard(start_language, telegram_id)
            await message.answer(
                i18n_get_text(start_language, "lang.select_title"),
                reply_markup=get_language_keyboard(start_language) if is_new_user else keyboard,
            )
            return

    # GIFT ACTIVATION: Обработка подарочной ссылки /start gift_XXXXX
    if payload and payload.startswith("gift_"):
        gift_code = payload[5:]  # Убираем "gift_" префикс
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

    # 1. REFERRAL REGISTRATION: Process ONLY for new users
    # Protects against: self-referral and existing users clicking referral links later
    referral_result = None
    if is_new_user:
        referral_result = await process_referral_on_first_interaction(message, telegram_id)
    else:
        # Existing user clicked a referral link — ignore and log
        if payload and payload.startswith("ref_"):
            logger.warning(
                "REFERRAL_BLOCKED_EXISTING_USER user=%s payload=%s",
                telegram_id, payload[:30]
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
