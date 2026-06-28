"""
User command: /start
"""
import logging
from datetime import datetime, timezone

import database
import config
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
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

    # STAGE GATE: новые пользователи в stage сначала выбирают «пользователь /
    # разработчик». Пользователь — редирект на prod-бот по реф-ссылке, разработчик —
    # продолжение во flow. В prod этот блок никогда не срабатывает.
    if config.IS_STAGE and is_new_user:
        await _show_stage_gate(message)
        return
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
    
    # SITE LINK: Обработка привязки с сайта /start <telegramLinkToken>
    # Сайт генерирует ссылку t.me/atlassecure_bot?start=<token>
    # Бот вызывает POST /api/bot/link чтобы привязать telegram_id к аккаунту сайта
    if message.text:
        start_parts = message.text.strip().split(maxsplit=1)
        if len(start_parts) > 1:
            payload = start_parts[1]
            # Токен привязки — не ref_, не gift_ и не bgift_ (буквенно-цифровой, 10-64 символа)
            if (not payload.startswith("ref_")
                    and not payload.startswith("gift_")
                    and not payload.startswith("bgift_")
                    and len(payload) >= 10
                    and len(payload) <= 64
                    and payload.replace("_", "").replace("-", "").isalnum()):
                try:
                    from app.services.site_sync import (
                        link_telegram_account, sync_balance, sync_referrals,
                        is_enabled as _site_enabled,
                    )
                    if _site_enabled():
                        link_result = await link_telegram_account(payload, telegram_id)
                        if link_result:
                            logger.info("SITE_LINK_SUCCESS user=%s token=%s", telegram_id, payload[:16])
                            # Mark user as site-linked in local DB
                            pool = await database.get_pool()
                            async with pool.acquire() as conn:
                                await conn.execute(
                                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS site_linked BOOLEAN DEFAULT FALSE"
                                )
                                await conn.execute(
                                    "UPDATE users SET site_linked = TRUE WHERE telegram_id = $1",
                                    telegram_id,
                                )
                            # Sync data immediately after linking
                            sub = await database.get_subscription(telegram_id)
                            if sub and sub.get("expires_at"):
                                from app.services.site_sync import sync_subscription
                                exp_iso = sub["expires_at"].isoformat()
                                plan = (sub.get("subscription_type") or "basic").strip().lower()
                                await sync_subscription(telegram_id, exp_iso, plan)
                            await sync_balance(telegram_id)
                            await sync_referrals(telegram_id)
                            logger.info("SITE_LINK_FULL_SYNC user=%s", telegram_id)

                            await message.answer(
                                "✅ Сайт QoDev успешно привязан.\nТеперь синхронизация работает! ⚡️",
                                parse_mode="HTML",
                            )
                        else:
                            logger.warning("SITE_LINK_FAILED user=%s token=%s", telegram_id, payload[:16])
                except Exception as e:
                    logger.warning("SITE_LINK_ERROR user=%s error=%s", telegram_id, e)

    # BYPASS GIFT LINK: /start bgift_<CODE> — admin-created GB gift link.
    # Grants the configured bypass GB through Remnawave; one redemption per user.
    if message.text:
        start_parts = message.text.strip().split(maxsplit=1)
        if len(start_parts) > 1 and start_parts[1].startswith("bgift_"):
            bgift_code = start_parts[1][6:]  # Strip "bgift_" prefix
            if bgift_code and 4 <= len(bgift_code) <= 32 and bgift_code.isalnum():
                language = await resolve_user_language(telegram_id)
                try:
                    result = await database.redeem_bypass_gift_link(
                        code=bgift_code,
                        telegram_id=telegram_id,
                    )
                    status = result.get("status")
                    # Default keyboard for non-success outcomes (errors).
                    keyboard = (
                        get_language_keyboard(language) if is_new_user
                        else await get_main_menu_keyboard(language, telegram_id)
                    )

                    if status == "success":
                        gb = result.get("gb_amount") or 0
                        link_id = (result.get("link") or {}).get("id")
                        # Grant GB via Remnawave (creates account if user has none).
                        # We need to make sure there's an active subscription row so
                        # set_remnawave_uuid (WHERE status='active') can persist the
                        # UUID. But ensure_bypass_only_subscription clobbers an
                        # existing active row's expires_at to +10y — so only call it
                        # when the user has NO active subscription.
                        from app.services.remnawave_service import add_bypass_traffic
                        extra_bytes = int(gb) * 1024 * 1024 * 1024
                        granted = False
                        try:
                            existing_active = await database.get_subscription(telegram_id)
                            if not existing_active:
                                await database.ensure_bypass_only_subscription(telegram_id)
                            granted = await add_bypass_traffic(
                                telegram_id=telegram_id,
                                extra_bytes=extra_bytes,
                                subscription_type="basic",
                                subscription_end=None,
                                period_days=30,
                            )
                        except Exception as rmn_err:
                            logger.exception(
                                "BGIFT_REMNAWAVE_FAIL user=%s code=%s err=%s",
                                telegram_id, bgift_code, rmn_err,
                            )

                        if granted:
                            text = i18n_get_text(
                                language, "bypass_gift.activated",
                                gb=gb,
                            )
                            # Success keyboard: dedicated "Connect Bypass" button
                            # leading to the gift-only setup flow.
                            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
                            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(
                                    text=i18n_get_text(language, "bypass_gift.connect_btn"),
                                    callback_data="bgift_setup",
                                )],
                            ])
                            logger.info(
                                "BGIFT_REDEEMED user=%s code=%s gb=%s",
                                telegram_id, bgift_code, gb,
                            )
                        else:
                            # Remnawave failed — roll back the redemption record so
                            # the user can retry without hitting the per-user
                            # uniqueness guard. Logs flag the issue for admin.
                            if link_id is not None:
                                try:
                                    rolled_back = await database.rollback_bypass_gift_redemption(
                                        link_id, telegram_id,
                                    )
                                    logger.error(
                                        "BGIFT_REMNAWAVE_FAIL_ROLLBACK user=%s code=%s gb=%s rolled_back=%s",
                                        telegram_id, bgift_code, gb, rolled_back,
                                    )
                                except Exception as rb_err:
                                    logger.exception(
                                        "BGIFT_ROLLBACK_FAIL user=%s code=%s err=%s",
                                        telegram_id, bgift_code, rb_err,
                                    )
                            text = i18n_get_text(language, "bypass_gift.error_remnawave")
                        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
                        return

                    error_keys = {
                        "already_redeemed": "bypass_gift.error_already_redeemed",
                        "expired": "bypass_gift.error_expired",
                        "max_uses_reached": "bypass_gift.error_max_uses",
                        "deleted": "bypass_gift.error_not_found",
                        "not_found": "bypass_gift.error_not_found",
                    }
                    text = i18n_get_text(
                        language, error_keys.get(status, "bypass_gift.error_not_found"),
                    )
                    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
                    logger.info(
                        "BGIFT_REDEMPTION_FAILED user=%s code=%s status=%s",
                        telegram_id, bgift_code, status,
                    )
                    return
                except Exception as e:
                    logger.exception(
                        "BGIFT_REDEMPTION_ERROR user=%s code=%s err=%s",
                        telegram_id, bgift_code, e,
                    )
                    text = i18n_get_text(language, "bypass_gift.error_not_found")
                    keyboard = (
                        get_language_keyboard(language) if is_new_user
                        else await get_main_menu_keyboard(language, telegram_id)
                    )
                    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
                    return

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
                        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
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
                    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
                    return

    # SHARE-DISCOUNT LINK: /start refd_<code> — recipient gets 30%/24h
    # discount on basic/plus/combo. Lifetime-once per telegram_id (claim
    # tracked in `referral_share_discount_claims`). For new users we ALSO
    # set up the referral relationship (immutable), per product spec.
    # Handled BEFORE the regular `ref_` branch — `refd_` doesn't match
    # `ref_` via startswith, but order is also clearer this way.
    if message.text:
        start_parts = message.text.strip().split(maxsplit=1)
        if len(start_parts) > 1 and start_parts[1].startswith("refd_"):
            refd_code = start_parts[1][5:]  # strip "refd_"
            handled = await _handle_share_discount_start(
                message, state, telegram_id, refd_code, is_new_user,
            )
            if handled:
                return  # Already rendered final screen — done.

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
                # Текущий тир-процент реферрера для подстановки в пуш.
                ref_stats = await database.get_referral_statistics(referrer_id)
                ref_percent = int(ref_stats.get("cashback_percent", 10))
                from app.services.notifications.loyalty_pushes import pick_signup_push
                notification_text = pick_signup_push(ref_percent)

                await message.bot.send_message(
                    chat_id=referrer_id,
                    text=notification_text,
                    parse_mode="HTML",
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
    await message.answer(text, reply_markup=get_language_keyboard(start_language), parse_mode="HTML")


_SHARE_DISCOUNT_PERCENT = 30
_SHARE_DISCOUNT_HOURS = 24


async def _handle_share_discount_start(
    message: Message,
    state: FSMContext,
    telegram_id: int,
    refd_code: str,
    is_new_user: bool,
) -> bool:
    """Process /start refd_<code> — share-discount activation.

    Возвращает True, если экран отрендерен полностью и cmd_start должен
    выйти. False — продолжаем стандартный flow (например, payload
    оказался кривой и мы хотим показать обычное приветствие).

    Семантика:
      • self-referral → блок + main-меню (нечего здесь покупать)
      • lifetime claim уже есть → notice + экран тарифов (юзер всё
        равно мог прийти выбирать тариф; если активная скидка ещё
        жива — увидит её на экране автоматически)
      • новый юзер → закрепить referrer_id через стандартный pipeline
        (process_referral_registration с конвертацией refd_→ref_)
      • выдать 30% / 24ч personal discount (если нет более выгодной)
      • записать в referral_share_discount_claims
      • показать notice + экран тарифов (скидка автоматически
        отрисуется в ценах — _open_buy_screen зовёт get_user_discount)
    """
    from datetime import timedelta
    from app.services.referrals import process_referral_registration
    from app.handlers.common.screens import show_tariffs_main_screen

    # Sanity: код — alphanumeric, 4–12 символов (наш формат 6).
    if not refd_code or len(refd_code) > 32 or not refd_code.replace("_", "").isalnum():
        logger.warning(
            "REFDC_INVALID_PAYLOAD user=%s code=%s",
            telegram_id, refd_code[:30],
        )
        return False  # fall through to normal /start

    language = await resolve_user_language(telegram_id)

    # Найти владельца кода. Сначала opaque referral_code, затем legacy
    # numeric telegram_id (та же логика, что в process_referral_registration).
    referrer_user = await database.find_user_by_referral_code(refd_code)
    referrer_id: int | None = None
    if referrer_user:
        referrer_id = referrer_user.get("telegram_id")
    else:
        try:
            maybe = int(refd_code)
            legacy = await database.get_user(maybe)
            if legacy:
                referrer_id = maybe
        except (ValueError, TypeError):
            pass

    if referrer_id is None:
        logger.warning(
            "REFDC_UNKNOWN_CODE user=%s code=%s — falling back to normal /start",
            telegram_id, refd_code[:30],
        )
        return False

    # Self-referral block — main-меню, чтобы не подталкивать к покупке
    # через манипуляцию собственной ссылкой.
    if referrer_id == telegram_id:
        logger.info("REFDC_SELF_BLOCKED user=%s", telegram_id)
        text = i18n_get_text(language, "share_discount.self_blocked")
        keyboard = await get_main_menu_keyboard(language, telegram_id)
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        return True

    # Lifetime-once guard. Покажем notice отдельным сообщением, потом
    # отрисуем экран тарифов — юзер пришёл сюда явно за подпиской.
    if await database.has_claimed_referral_share_discount(telegram_id):
        logger.info("REFDC_ALREADY_CLAIMED user=%s", telegram_id)
        await message.answer(
            i18n_get_text(language, "share_discount.already_claimed"),
            parse_mode="HTML",
        )
        await show_tariffs_main_screen(message, state)
        return True

    # Новый юзер → закрепить referrer_id через стандартный пайплайн.
    # Конвертируем refd_<code> → ref_<code>, чтобы переиспользовать
    # validation/loop-detection/audit, который уже отлажен.
    if is_new_user:
        try:
            await process_referral_registration(telegram_id, f"ref_{refd_code}")
        except Exception:
            logger.exception("REFDC_REFERRAL_REGISTRATION_FAIL user=%s", telegram_id)

    # Выдать personal-discount. Если у юзера уже есть скидка ≥30% —
    # не перезаписываем, оставляем выгоднее. create_user_discount
    # делает ON CONFLICT DO UPDATE безусловно, поэтому проверяем сами.
    expires_at = datetime.now(timezone.utc) + timedelta(hours=_SHARE_DISCOUNT_HOURS)
    existing = await database.get_user_discount(telegram_id)
    keep_existing = bool(
        existing and existing.get("discount_percent", 0) >= _SHARE_DISCOUNT_PERCENT
    )
    if not keep_existing:
        try:
            await database.create_user_discount(
                telegram_id=telegram_id,
                discount_percent=_SHARE_DISCOUNT_PERCENT,
                expires_at=expires_at,
                created_by=referrer_id,
            )
        except Exception:
            logger.exception("REFDC_DISCOUNT_CREATE_FAIL user=%s", telegram_id)
            # Не критично — продолжаем, claim всё равно фиксируем чтобы
            # юзер не мог попытаться снова и снова.

    recorded = await database.record_referral_share_discount_claim(
        telegram_id=telegram_id,
        referrer_id=referrer_id,
        discount_percent=_SHARE_DISCOUNT_PERCENT,
        duration_hours=_SHARE_DISCOUNT_HOURS,
        expires_at=expires_at,
    )
    if not recorded:
        # Race-condition: между нашим has_claimed-чеком и INSERT'ом
        # успели вставить параллельным процессом. Покажем notice +
        # тарифы (скидка от первого «победителя» уже в DB).
        logger.info("REFDC_RACE_LOST user=%s — claim insert returned 0", telegram_id)
        await message.answer(
            i18n_get_text(language, "share_discount.already_claimed"),
            parse_mode="HTML",
        )
        await show_tariffs_main_screen(message, state)
        return True

    logger.info(
        "REFDC_CLAIMED user=%s referrer=%s pct=%s hours=%s",
        telegram_id, referrer_id, _SHARE_DISCOUNT_PERCENT, _SHARE_DISCOUNT_HOURS,
    )

    # Notice об активации + экран тарифов. _open_buy_screen внутри
    # show_tariffs_main_screen сам подтянет get_user_discount и
    # отрисует уже скидочные цены — двойной работы нет.
    await message.answer(
        i18n_get_text(language, "share_discount.activated"),
        parse_mode="HTML",
    )
    await show_tariffs_main_screen(message, state)
    return True


# ── STAGE-only: new-user gate ──────────────────────────────────────────────

async def _show_stage_gate(message: Message) -> None:
    """Greeting screen shown on the FIRST /start to any new user in STAGE.

    The «Пользователь» button is a URL deep-link to the production bot with
    our referral payload — clicking it never touches the stage DB. The
    «Разработчик» button creates the user record locally and continues to
    the normal flow (see callback_stage_gate_dev).
    """
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="👤 Пользователь",
            url="https://t.me/atlassecure_bot?start=ref_RC26QG",
        )],
        [InlineKeyboardButton(
            text="💻 Разработчик",
            callback_data="stage_gate:dev",
        )],
    ])
    text = (
        "Привет 👋\n\n"
        "Ты разработчик Atlas Secure или пользователь?\n"
        "Выбери вариант ниже 👇"
    )
    await message.answer(text, reply_markup=keyboard)


@user_router.callback_query(F.data == "stage_gate:dev")
async def callback_stage_gate_dev(callback: CallbackQuery, state: FSMContext):
    """«Разработчик» — создаём user-запись и пускаем в обычный главный экран."""
    if not config.IS_STAGE:
        await callback.answer()
        return
    await callback.answer()

    telegram_id = callback.from_user.id

    if not database.DB_READY:
        # Degraded: just render the menu without persisting anything.
        language = await resolve_user_language(telegram_id)
        text = i18n_get_text(language, "main.welcome")
        keyboard = await get_main_menu_keyboard(language, telegram_id)
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.bot.send_message(telegram_id, text, reply_markup=keyboard, parse_mode="HTML")
        return

    user = await database.get_user(telegram_id)
    if user is None:
        username = safe_resolve_username(callback.from_user, "ru", telegram_id)
        if username and len(username) > 64:
            username = username[:64]
        try:
            await database.create_user(telegram_id, username, "ru")
        except Exception as e:
            logger.warning(f"STAGE_GATE_DEV: create_user failed user={telegram_id}: {e}")

    language = await resolve_user_language(telegram_id)
    text = i18n_get_text(language, "main.welcome")
    keyboard = await get_main_menu_keyboard(language, telegram_id)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.bot.send_message(telegram_id, text, reply_markup=keyboard, parse_mode="HTML")
