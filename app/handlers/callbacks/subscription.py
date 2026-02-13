"""
Subscription-related callback handlers: toggle_auto_renew, activate_trial,
menu_profile, menu_vip_access, renewal_pay, subscription_history.
"""
import logging
import time
from datetime import datetime, timedelta, timezone

import config
import database
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.services.referrals import activate_referral
from app.core.system_state import (
    SystemState,
    healthy_component,
    degraded_component,
    unavailable_component,
)
from app.core.rate_limit import check_rate_limit
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.utils import (
    safe_edit_text,
    format_text_with_incident,
    safe_resolve_username_from_db,
)
from app.handlers.common.keyboards import (
    get_profile_keyboard,
    get_main_menu_keyboard,
    get_back_keyboard,
)
from app.handlers.common.screens import show_profile
from app.handlers.common.states import PromoCodeInput

subscription_router = Router()
logger = logging.getLogger(__name__)


@subscription_router.callback_query(F.data.startswith("toggle_auto_renew:"))
async def callback_toggle_auto_renew(callback: CallbackQuery):
    """Включить/выключить автопродление"""
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    action = callback.data.split(":")[1]

    pool = await database.get_pool()
    async with pool.acquire() as conn:
        auto_renew = (action == "on")
        await conn.execute(
            "UPDATE subscriptions SET auto_renew = $1 WHERE telegram_id = $2",
            auto_renew, telegram_id
        )

    language = await resolve_user_language(telegram_id)

    if auto_renew:
        text = i18n_get_text(language, "subscription.auto_renew_enabled_toast")
    else:
        text = i18n_get_text(language, "subscription.auto_renew_disabled_toast")

    await callback.answer(text, show_alert=True)

    # Обновляем экран профиля
    await show_profile(callback, language)


@subscription_router.callback_query(F.data == "activate_trial")
async def callback_activate_trial(callback: CallbackQuery, state: FSMContext):
    """Активация пробного периода на 3 дня"""
    # READ-ONLY system state awareness (informational only, does not affect flow)
    try:
        now = datetime.now(timezone.utc)
        db_ready = database.DB_READY

        # STEP 1.1 - RUNTIME GUARDRAILS: SystemState is READ-ONLY snapshot
        if db_ready:
            db_component = healthy_component(last_checked_at=now)
        else:
            db_component = unavailable_component(
                error="DB not ready (degraded mode)",
                last_checked_at=now
            )

        if config.VPN_ENABLED and config.XRAY_API_URL:
            vpn_component = healthy_component(last_checked_at=now)
        else:
            vpn_component = degraded_component(
                error="VPN API not configured",
                last_checked_at=now
            )

        payments_component = healthy_component(last_checked_at=now)
        system_state = SystemState(
            database=db_component,
            vpn_api=vpn_component,
            payments=payments_component,
        )

        if system_state.is_degraded:
            logger.info(
                f"[DEGRADED] system_state detected during callback_activate_trial "
                f"(user={callback.from_user.id}, optional components degraded)"
            )
            _degradation_notice = True
        else:
            _degradation_notice = False
    except Exception:
        _degradation_notice = False

    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # STEP 6 — F3: RATE LIMITING (HUMAN & BOT SAFETY)
    is_allowed, rate_limit_message = check_rate_limit(telegram_id, "trial_activate")
    if not is_allowed:
        await callback.answer(rate_limit_message or i18n_get_text(language, "common.rate_limit_message"), show_alert=True)
        return

    # КРИТИЧНО: Проверяем eligibility перед активацией
    is_eligible = await database.is_eligible_for_trial(telegram_id)
    if not is_eligible:
        error_text = i18n_get_text(language, "main.trial_not_available")
        await callback.answer(error_text, show_alert=True)
        logger.warning(f"Trial activation attempted by ineligible user: {telegram_id}")
        return

    await callback.answer()

    try:
        duration = timedelta(days=3)
        now = datetime.now(timezone.utc)
        trial_expires_at = now + duration

        success = await database.mark_trial_used(telegram_id, trial_expires_at)
        if not success:
            raise Exception("Failed to mark trial as used")

        result = await database.grant_access(
            telegram_id=telegram_id,
            duration=duration,
            source="trial",
            admin_telegram_id=None
        )

        uuid = result.get("uuid")
        vpn_key = result.get("vless_url")
        subscription_end = result.get("subscription_end")

        if not uuid or not vpn_key:
            raise Exception("Failed to create VPN access for trial")

        # 2. REFERRAL LIFECYCLE: Activate referral (REGISTERED → ACTIVATED)
        try:
            activation_result = await activate_referral(telegram_id, activation_type="trial")
            if activation_result.get("success") and activation_result.get("was_activated"):
                logger.info(
                    f"REFERRAL_ACTIVATED [referrer={activation_result.get('referrer_id')}, "
                    f"referred={telegram_id}, type=trial, state=ACTIVATED]"
                )

                referrer_id = activation_result.get("referrer_id")
                if referrer_id:
                    try:
                        referrer_user_for_notif = await database.get_user(referrer_id)
                        referrer_language_notif = await resolve_user_language(referrer_id)
                        referred_user = await database.get_user(telegram_id)
                        referred_username = safe_resolve_username_from_db(
                            referred_user, referrer_language_notif, telegram_id
                        )

                        user_fallback_text = i18n_get_text(referrer_language_notif, "common.user")
                        if referred_username and not referred_username.startswith("ID:") and referred_username != user_fallback_text:
                            referred_display = f"@{referred_username}" if not referred_username.startswith("@") else referred_username
                        else:
                            referred_display = referred_username

                        first_payment_msg_notif = i18n_get_text(referrer_language_notif, "referral.first_payment_notification")
                        title_trial = i18n_get_text(referrer_language_notif, "referral.trial_activated_title")
                        user_line_trial = i18n_get_text(referrer_language_notif, "referral.trial_activated_user", user=referred_display)
                        trial_period_line = i18n_get_text(referrer_language_notif, "referral.trial_period")
                        notification_text = f"{title_trial}\n\n{user_line_trial}\n{trial_period_line}\n\n{first_payment_msg_notif}"

                        await callback.bot.send_message(
                            chat_id=referrer_id,
                            text=notification_text
                        )

                        logger.info(
                            f"REFERRAL_NOTIFICATION_SENT [type=trial_activation, referrer={referrer_id}, "
                            f"referred={telegram_id}, referred_display={referred_display}]"
                        )
                    except Exception as e:
                        logger.warning(
                            "NOTIFICATION_FAILED",
                            extra={
                                "type": "trial_activation",
                                "referrer": referrer_id,
                                "referred": telegram_id,
                                "error": str(e)
                            }
                        )
        except Exception as e:
            logger.warning(f"Failed to activate referral for trial: user={telegram_id}, error={e}")

        logger.info(
            f"trial_activated: user={telegram_id}, trial_used_at={now.isoformat()}, "
            f"trial_expires_at={trial_expires_at.isoformat()}, subscription_expires_at={subscription_end.isoformat()}, "
            f"uuid={uuid[:8]}..."
        )

        success_text = i18n_get_text(
            language, "main.trial_activated_text",
            vpn_key=vpn_key,
            expires_date=subscription_end.strftime("%d.%m.%Y %H:%M")
        )

        try:
            if _degradation_notice:
                success_text += "\n\n⏳ Возможны небольшие задержки"
        except NameError:
            pass

        await callback.message.answer(success_text, parse_mode="HTML")

        try:
            await callback.message.answer(f"<code>{vpn_key}</code>", parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Failed to send VPN key with HTML tags: {e}. Sending plain text.")
            await callback.message.answer(f"🔑 {vpn_key}")

        # Обновляем главное меню (кнопка trial должна исчезнуть)
        text = i18n_get_text(language, "main.welcome")
        text = await format_text_with_incident(text, language)
        keyboard = await get_main_menu_keyboard(language, telegram_id)
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)

    except Exception as e:
        logger.exception(f"Error activating trial for user {telegram_id}: {e}")
        error_text = i18n_get_text(language, "main.trial_activation_error")
        await callback.message.answer(error_text)


@subscription_router.callback_query(F.data == "menu_profile", StateFilter(default_state))
@subscription_router.callback_query(F.data == "menu_profile")
async def callback_profile(callback: CallbackQuery, state: FSMContext):
    """Мой профиль - работает независимо от FSM состояния"""
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return

    # CRITICAL FIX: Очищаем FSM state при переходе на профиль
    current_state = await state.get_state()
    if current_state == PromoCodeInput.waiting_for_promo.state:
        await state.clear()

    # REAL-TIME EXPIRATION CHECK: Проверяем и отключаем истекшие подписки сразу
    await database.check_and_disable_expired_subscription(callback.from_user.id)
    telegram_id = callback.from_user.id

    # Немедленная обратная связь пользователю
    await callback.answer()

    try:
        current_state = await state.get_state()
        if current_state is not None:
            await state.clear()
            logger.debug(f"Cleared FSM state for user {telegram_id}, was: {current_state}")
    except Exception as e:
        logger.debug(f"FSM state clear failed (may be already clear): {e}")

    try:
        logger.info(f"Opening profile for user {telegram_id}")

        language = await resolve_user_language(telegram_id)

        await show_profile(callback, language)

        logger.info(f"Profile opened successfully for user {telegram_id}")
    except Exception as e:
        logger.exception(f"Error opening profile for user {telegram_id}: {e}")
        try:
            user = await database.get_user(telegram_id)
            language = await resolve_user_language(callback.from_user.id)
            error_text = i18n_get_text(language, "errors.profile_load")
            await callback.message.answer(error_text)
        except Exception as e2:
            logger.exception(f"Error sending error message to user {telegram_id}: {e2}")


@subscription_router.callback_query(F.data == "menu_vip_access")
async def callback_vip_access(callback: CallbackQuery):
    """Обработчик кнопки 'VIP-доступ'"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    is_vip = await database.is_vip_user(telegram_id)

    text = i18n_get_text(language, "main.vip_access_text", "vip_access_text")

    if is_vip:
        text += "\n\n" + i18n_get_text(language, "main.vip_status_active")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.contact_manager_button"),
            url="https://t.me/asc_support"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_profile"
        )]
    ])

    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
    await callback.answer()


@subscription_router.callback_query(F.data.startswith("renewal_pay:"))
async def callback_renewal_pay(callback: CallbackQuery):
    """Обработчик кнопки оплаты продления - отправляет invoice через Telegram Payments"""
    tariff_key = callback.data.split(":")[1]
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    if not config.TG_PROVIDER_TOKEN:
        language = await resolve_user_language(telegram_id)
        await callback.answer(i18n_get_text(language, "errors.payments_unavailable"), show_alert=True)
        return

    if tariff_key not in config.TARIFFS:
        error_msg = f"Invalid tariff_key '{tariff_key}' for user {telegram_id}. Valid tariffs: {list(config.TARIFFS.keys())}"
        logger.error(error_msg)
        language = await resolve_user_language(telegram_id)
        await callback.answer(i18n_get_text(language, "errors.tariff"), show_alert=True)
        return

    if 30 not in config.TARIFFS[tariff_key]:
        error_msg = f"Period 30 days not found in tariff '{tariff_key}' for user {telegram_id}"
        logger.error(error_msg)
        language = await resolve_user_language(telegram_id)
        await callback.answer(i18n_get_text(language, "errors.tariff"), show_alert=True)
        return

    tariff_data = config.TARIFFS[tariff_key][30]
    base_price = tariff_data["price"]

    is_vip = await database.is_vip_user(telegram_id)

    if is_vip:
        amount = int(base_price * 0.70)
    else:
        personal_discount = await database.get_user_discount(telegram_id)

        if personal_discount:
            discount_percent = personal_discount["discount_percent"]
            amount = int(base_price * (1 - discount_percent / 100))
        else:
            amount = base_price

    payload = f"renew:{telegram_id}:{tariff_key}:{int(time.time())}"

    period_days = 30
    months = period_days // 30
    if months == 1:
        period_text = "1 месяц"
    elif months in [2, 3, 4]:
        period_text = f"{months} месяца"
    else:
        period_text = f"{months} месяцев"
    description = f"Atlas Secure VPN продление подписки на {period_text}"

    language = await resolve_user_language(telegram_id)
    prices = [LabeledPrice(label=i18n_get_text(language, "payment.label"), amount=amount * 100)]

    try:
        await callback.bot.send_invoice(
            chat_id=telegram_id,
            title="Atlas Secure VPN",
            description=description,
            payload=payload,
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=prices
        )
        await callback.answer()
    except Exception as e:
        logger.exception(f"Error sending invoice for renewal: {e}")
        language = await resolve_user_language(telegram_id)
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@subscription_router.callback_query(F.data == "subscription_history")
async def callback_subscription_history(callback: CallbackQuery):
    """История подписок"""
    await callback.answer()

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    history = await database.get_subscription_history(telegram_id, limit=5)

    if not history:
        text = i18n_get_text(language, "subscription.history_empty", "subscription_history_empty")
        await callback.message.answer(text)
        return

    text = i18n_get_text(language, "subscription.history", "subscription_history") + "\n\n"

    action_type_map = {
        "purchase": i18n_get_text(language, "subscription.history_action_purchase", "subscription_history_action_purchase"),
        "renewal": i18n_get_text(language, "subscription.history_action_renewal", "subscription_history_action_renewal"),
        "reissue": i18n_get_text(language, "subscription.history_action_reissue", "subscription_history_action_reissue"),
        "manual_reissue": i18n_get_text(language, "subscription.history_action_manual_reissue", "subscription_history_action_manual_reissue"),
    }

    for record in history:
        start_date = record["start_date"]
        if isinstance(start_date, str):
            start_date = datetime.fromisoformat(start_date)
        start_str = start_date.strftime("%d.%m.%Y")

        end_date = record["end_date"]
        if isinstance(end_date, str):
            end_date = datetime.fromisoformat(end_date)
        end_str = end_date.strftime("%d.%m.%Y")

        action_type = record["action_type"]
        action_text = action_type_map.get(action_type, action_type)

        text += f"• {start_str} — {action_text}\n"

        if action_type in ["purchase", "reissue", "manual_reissue"]:
            key_label = i18n_get_text(language, "subscription.history_key_label")
            text += f"  {key_label} {record['vpn_key']}\n"

        expires_label = i18n_get_text(language, "subscription.history_expires")
        text += f"  {expires_label} {end_str}\n\n"

    await callback.message.answer(text, reply_markup=get_back_keyboard(language))
