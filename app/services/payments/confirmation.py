"""
Shared payment confirmation logic for all webhook providers.

Eliminates duplicate code between platega_service and cryptobot_service.
Each provider handles auth/signature verification, then delegates here.
"""
import asyncio
import json
import logging
from typing import Optional, Dict, Any

import asyncpg
import config
import database
from aiogram import Bot

logger = logging.getLogger(__name__)


class TransientPaymentError(Exception):
    """Transient error during payment processing (DB timeout, connection error).

    Webhook handler should return HTTP 500 so the payment provider retries.
    """
    pass


async def process_confirmed_payment(
    provider: str,
    purchase_id: str,
    amount_rubles: float,
    invoice_id: str,
    telegram_id: int,
    bot: Bot,
) -> dict:
    """
    Shared logic for processing a confirmed payment webhook.

    Called after provider-specific auth verification and payload extraction.

    Args:
        provider: Payment provider name ("platega", "cryptobot")
        purchase_id: Internal purchase ID
        amount_rubles: Payment amount in RUB
        invoice_id: Provider's transaction/invoice ID
        telegram_id: Buyer's Telegram ID
        bot: Bot instance for sending confirmation messages

    Returns:
        Response dict with "status" key ("ok", "already_processed", "error")
    """
    try:
        # Check if this is a notification-only purchase (no subscription to activate).
        # Accept both 'pending' and 'expired' — user may have started a new purchase
        # flow which marked this one expired before the webhook arrived. The payment
        # itself is still valid and must not be dropped. Consistent with
        # lookup_pending_purchase() upstream and finalize_purchase()'s recovery path.
        pending = await database.get_pending_purchase_by_id(purchase_id, check_expiry=False)
        if not pending or pending.get("telegram_id") != telegram_id:
            logger.error(f"{provider} webhook: pending purchase not found: {purchase_id}")
            return {"status": "error", "message": "Purchase not found"}

        _purchase_type = pending.get("purchase_type") or "subscription"
        _tariff = pending.get("tariff") or ""

        # Stars / Premium / Apple ID — just mark paid + send notifications (no finalize)
        if _purchase_type in ("telegram_stars", "telegram_premium") or _tariff.startswith("apple_id_"):
            marked = await database.mark_pending_purchase_paid(purchase_id)
            if not marked:
                logger.info(
                    f"{provider} webhook: {_purchase_type} already finalized (concurrent webhook), "
                    f"purchase_id={purchase_id} — skipping notification to avoid duplicate"
                )
                return {"status": "already_processed", "purchase_id": purchase_id}
            logger.info(f"{provider} webhook: {_purchase_type} marked paid, purchase_id={purchase_id}")

            try:
                if _purchase_type == "telegram_stars":
                    from app.handlers.payments.telegram_stars_purchase import send_stars_success
                    await send_stars_success(bot, telegram_id, purchase_id, pending)
                elif _purchase_type == "telegram_premium":
                    from app.handlers.payments.telegram_premium import send_premium_success
                    await send_premium_success(bot, telegram_id, purchase_id, pending)
                elif _tariff.startswith("apple_id_"):
                    tariff_parts = _tariff.split("_")
                    region = tariff_parts[2] if len(tariff_parts) >= 3 else "usa"
                    nominal = int(tariff_parts[3]) if len(tariff_parts) >= 4 else 0
                    from app.handlers.callbacks.navigation import send_apple_id_success
                    await send_apple_id_success(bot, telegram_id, region, nominal, amount_rubles)
            except Exception as notif_err:
                logger.error(f"{provider} webhook: notification failed for {_purchase_type}: {notif_err}")

            return {"status": "ok", "purchase_id": purchase_id}

        result = await database.finalize_purchase(
            purchase_id=purchase_id,
            payment_provider=provider,
            amount_rubles=amount_rubles,
            invoice_id=str(invoice_id),
        )

        if not result or not result.get("success"):
            logger.error(f"{provider} webhook: finalize_purchase failed: {result}")
            raise Exception(f"finalize_purchase returned invalid result: {result}")

        payment_id = result["payment_id"]
        expires_at = result.get("expires_at")
        is_balance_topup = result.get("is_balance_topup", False)
        is_traffic_pack = result.get("is_traffic_pack", False)

        # Notification failure must NOT fail the payment — DB is already committed
        try:
            if is_traffic_pack:
                await _handle_traffic_pack_confirmation(
                    provider=provider,
                    bot=bot,
                    telegram_id=telegram_id,
                    payment_id=payment_id,
                    purchase_id=purchase_id,
                    traffic_gb=result.get("traffic_gb", 0),
                    tariff_type=result.get("tariff_type", ""),
                )
            else:
                await _send_confirmation(
                    provider=provider,
                    bot=bot,
                    telegram_id=telegram_id,
                    payment_id=payment_id,
                    purchase_id=purchase_id,
                    is_balance_topup=is_balance_topup,
                    amount_rubles=amount_rubles,
                    result=result,
                    expires_at=expires_at,
                )
        except Exception as notif_err:
            logger.error(
                f"PAYMENT_NOTIFICATION_FAILED: provider={provider}, user={telegram_id}, "
                f"purchase_id={purchase_id}, payment_id={payment_id}, "
                f"error={type(notif_err).__name__}: {notif_err} — payment was successful"
            )

        # Site sync (fire-and-forget — must not fail the payment)
        try:
            from app.services.site_sync import full_sync_after_payment, is_enabled as site_sync_enabled
            if site_sync_enabled() and not is_balance_topup and not is_traffic_pack:
                period_days = result.get("period_days", 30)
                tariff_type = result.get("tariff_type", "basic")
                asyncio.ensure_future(full_sync_after_payment(
                    telegram_id, period_days, tariff_type, amount_rubles, purchase_id,
                ))
        except Exception as sync_err:
            logger.warning("SITE_SYNC_FIRE_AND_FORGET_ERROR: %s", sync_err)

    except ValueError as e:
        logger.info(
            f"{provider} webhook: purchase already processed (ValueError): "
            f"purchase_id={purchase_id}, error={e}"
        )
        return {"status": "already_processed"}
    except (asyncpg.PostgresError, asyncio.TimeoutError, OSError) as e:
        # Transient infrastructure error — provider MUST retry
        logger.error(
            f"PAYMENT_TRANSIENT_ERROR: provider={provider}, user={telegram_id}, "
            f"purchase_id={purchase_id}, error={type(e).__name__}: {e}"
        )
        from app.services.admin_alerts import alert_payment_failure
        tariff, period_days = await _lookup_purchase_tariff(purchase_id)
        await alert_payment_failure(
            bot, provider, telegram_id, purchase_id, e, is_transient=True,
            amount_rubles=amount_rubles, tariff=tariff, period_days=period_days,
        )
        raise TransientPaymentError(
            f"Transient DB error during payment: {type(e).__name__}"
        ) from e
    except Exception as e:
        logger.exception(
            f"PAYMENT_PERMANENT_ERROR: provider={provider}, user={telegram_id}, "
            f"purchase_id={purchase_id}, error={e}"
        )
        from app.services.admin_alerts import alert_payment_failure
        tariff, period_days = await _lookup_purchase_tariff(purchase_id)
        await alert_payment_failure(
            bot, provider, telegram_id, purchase_id, e, is_transient=False,
            amount_rubles=amount_rubles, tariff=tariff, period_days=period_days,
        )
        return {"status": "error"}

    return {"status": "ok"}


def extract_purchase_id(payload_raw: Any) -> Optional[str]:
    """Extract purchase_id from webhook payload (JSON string or dict)."""
    if not payload_raw:
        return None
    try:
        if isinstance(payload_raw, str):
            payload_data = json.loads(payload_raw)
        else:
            payload_data = payload_raw
        return payload_data.get("purchase_id")
    except (json.JSONDecodeError, TypeError):
        return None


async def lookup_pending_purchase(
    provider: str,
    purchase_id: str,
) -> dict:
    """
    Look up pending purchase and validate status.

    Returns:
        {"status": "ok", "purchase": dict, "telegram_id": int} on success
        {"status": "not_found"|"already_processed"} on failure
    """
    pending_purchase = await database.get_pending_purchase_by_id(
        purchase_id, check_expiry=False
    )

    if not pending_purchase:
        logger.warning(f"{provider} webhook: purchase not found: purchase_id={purchase_id}")
        return {"status": "not_found"}

    telegram_id = pending_purchase["telegram_id"]
    purchase_status = pending_purchase.get("status")

    if purchase_status == "paid":
        logger.info(
            f"{provider} webhook: purchase already processed: "
            f"purchase_id={purchase_id}, status={purchase_status}"
        )
        return {"status": "already_processed"}

    if purchase_status not in ("pending", "expired"):
        logger.warning(
            f"{provider} webhook: unexpected purchase status: "
            f"purchase_id={purchase_id}, status={purchase_status}"
        )
        return {"status": "invalid_status"}

    if purchase_status == "expired":
        logger.info(
            f"{provider} webhook: recovering expired purchase (payment arrived after new purchase created): "
            f"purchase_id={purchase_id}"
        )

    return {
        "status": "ok",
        "purchase": pending_purchase,
        "telegram_id": telegram_id,
    }


async def _lookup_purchase_tariff(purchase_id: str) -> tuple:
    """Look up tariff and period_days from pending_purchases for alert context.

    Returns (tariff, period_days) or (None, None) on any failure.
    """
    try:
        row = await database.get_pending_purchase_by_id(purchase_id, check_expiry=False)
        if row:
            return row.get("tariff"), row.get("period_days")
    except Exception:
        pass
    return None, None


async def _send_confirmation(
    provider: str,
    bot: Bot,
    telegram_id: int,
    payment_id: int,
    purchase_id: str,
    is_balance_topup: bool,
    amount_rubles: float,
    result: dict,
    expires_at: Any,
) -> None:
    """Send payment confirmation message to user."""
    from app.services.language_service import resolve_user_language
    from app.i18n import get_text as i18n_get_text

    language = await resolve_user_language(telegram_id)

    if is_balance_topup:
        topup_amount = result.get("amount", amount_rubles)
        text = i18n_get_text(language, "main.balance_topup_success", amount=topup_amount)
        try:
            await bot.send_message(telegram_id, text, parse_mode="HTML")
        except Exception as send_err:
            logger.warning(
                f"{provider}: failed to send topup confirmation to user={telegram_id}: {send_err}"
            )
        logger.info(
            f"{provider} payment processed (balance topup): user={telegram_id}, "
            f"payment_id={payment_id}, amount={topup_amount} RUB"
        )
    else:
        expires_str = expires_at.strftime("%d.%m.%Y") if expires_at else "N/A"
        subscription_type = (result.get("subscription_type") or "basic").strip().lower()
        if subscription_type not in config.VALID_SUBSCRIPTION_TYPES:
            subscription_type = "basic"

        if config.is_biz_tariff(subscription_type):
            _label, _emoji = "Business", "🏢"
        elif subscription_type == "plus":
            _label, _emoji = "Plus", "⭐️"
        else:
            _label, _emoji = "Basic", "📦"

        text = i18n_get_text(
            language,
            "payment.success",
            f"🎉 Оплата получена!\n{_emoji} Тариф: {_label}\n📅 До: {expires_str}",
            tariff_icon=_emoji,
            tariff=_label,
            date=expires_str,
        )

        from app.handlers.common.keyboards import get_connect_keyboard

        try:
            await bot.send_message(
                telegram_id, text, reply_markup=get_connect_keyboard(), parse_mode="HTML"
            )
        except Exception as send_err:
            logger.warning(
                f"{provider}: failed to send subscription confirmation to user={telegram_id}: {send_err}"
            )

        logger.info(
            f"{provider} payment processed: user={telegram_id}, payment_id={payment_id}, "
            f"purchase_id={purchase_id}, subscription_activated=True"
        )

        # Fire-and-forget: create or renew Remnawave bypass user
        # Skip for combo purchases — combo traffic is managed separately
        is_combo = result.get("is_combo", False)
        try:
            from app.services.remnawave_service import renew_remnawave_user_bg
            if expires_at and subscription_type not in ("trial", "telegram_premium", "telegram_stars") + config.BIZ_TARIFFS and not is_combo:
                _pd = result.get("period_days", 30) or 30
                renew_remnawave_user_bg(telegram_id, subscription_type, expires_at, period_days=_pd)
        except Exception as rmn_err:
            logger.warning("REMNAWAVE_HOOK_FAIL: provider=%s tg=%s %s", provider, telegram_id, rmn_err)

        # Combo: add bypass traffic
        if is_combo:
            try:
                _pd = result.get("period_days", 30) or 30
                combo_key = f"combo_{subscription_type}"
                combo_info = config.COMBO_TARIFFS.get(combo_key, {}).get(_pd)
                if combo_info:
                    combo_gb = combo_info["gb"]
                    traffic_bytes = combo_gb * 1024**3
                    from app.services.remnawave_service import add_bypass_traffic
                    rmn_ok = await add_bypass_traffic(
                        telegram_id,
                        traffic_bytes,
                        subscription_type=subscription_type,
                        subscription_end=expires_at,
                        period_days=_pd,
                    )
                    if rmn_ok:
                        await database.record_traffic_purchase(telegram_id, combo_gb, 0)
                        logger.info("COMBO_BYPASS_TRAFFIC_ADDED: provider=%s user=%s gb=%s", provider, telegram_id, combo_gb)
                else:
                    logger.warning("COMBO_TARIFF_NOT_FOUND: provider=%s user=%s combo_key=%s period=%s", provider, telegram_id, combo_key, _pd)
            except Exception as combo_err:
                logger.error("COMBO_BYPASS_TRAFFIC_ERROR: provider=%s user=%s error=%s", provider, telegram_id, combo_err)


async def _handle_traffic_pack_confirmation(
    provider: str,
    bot: Bot,
    telegram_id: int,
    payment_id: int,
    purchase_id: str,
    traffic_gb: int,
    tariff_type: str = "",
) -> None:
    """Send traffic pack purchase confirmation and add traffic via Remnawave."""
    from app.services.language_service import resolve_user_language
    from app.i18n import get_text as i18n_get_text

    language = await resolve_user_language(telegram_id)
    _is_bypass = bool(tariff_type and tariff_type.startswith("bypass_"))

    # Bypass-only: ensure subscription row + Remnawave user exist
    if _is_bypass:
        await database.ensure_bypass_only_subscription(telegram_id)

    # Add traffic via Remnawave (create user if stale/missing)
    rmn_success = False
    pack = config.TRAFFIC_PACKS.get(traffic_gb) or config.TRAFFIC_PACKS_EXTENDED.get(traffic_gb)
    if pack:
        traffic_bytes = pack["bytes"]
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        if rmn_uuid:
            try:
                from app.services.remnawave_service import add_traffic
                rmn_success = await add_traffic(telegram_id, traffic_bytes)
            except Exception as rmn_err:
                logger.error(
                    "TRAFFIC_PACK_REMNAWAVE_ERROR: provider=%s tg=%s gb=%s error=%s",
                    provider, telegram_id, traffic_gb, rmn_err,
                )
        if not rmn_success:
            # No UUID or stale (404) — clear and create fresh
            if rmn_uuid:
                await database.clear_remnawave_uuid(telegram_id)
                from app.services.happ_crypto import invalidate_crypto_link
                await invalidate_crypto_link(telegram_id)
            try:
                from app.services import remnawave_service
                from datetime import datetime, timezone, timedelta
                far_future = datetime.now(timezone.utc) + timedelta(days=3650)
                await remnawave_service.create_remnawave_user(
                    telegram_id, "basic", far_future,
                    traffic_limit_override=traffic_bytes,
                )
                rmn_success = True
                logger.info("BYPASS_REMNAWAVE_USER_CREATED provider=%s user=%s gb=%s", provider, telegram_id, traffic_gb)
            except Exception as rmn_err:
                logger.error(
                    "TRAFFIC_PACK_REMNAWAVE_CREATE_ERROR: provider=%s tg=%s gb=%s error=%s",
                    provider, telegram_id, traffic_gb, rmn_err,
                )
    else:
        logger.error(
            "TRAFFIC_PACK_INVALID_GB: provider=%s tg=%s gb=%s purchase=%s — pack not found in config",
            provider, telegram_id, traffic_gb, purchase_id,
        )

    # Bypass-only: activate 3-day trial if eligible
    _trial_activated = False
    if _is_bypass:
        try:
            from app.services.trials import service as trial_service
            if await trial_service.is_trial_available(telegram_id):
                await trial_service.activate_trial(telegram_id)
                _trial_activated = True
                logger.info("BYPASS_TRIAL_ACTIVATED provider=%s user=%s", provider, telegram_id)
        except Exception as trial_err:
            logger.warning("BYPASS_TRIAL_FAIL provider=%s user=%s: %s", provider, telegram_id, trial_err)

    if _is_bypass:
        text = i18n_get_text(language, "bypass.purchase_success", gb=traffic_gb)
        if _trial_activated:
            text += "\n\n" + i18n_get_text(language, "bypass.trial_activated")
    elif rmn_success:
        text = i18n_get_text(language, "traffic.purchase_success", gb=traffic_gb, price="")
    else:
        text = i18n_get_text(language, "traffic.purchase_success", gb=traffic_gb, price="")
        text += "\n\n⚠️ Активация трафика задерживается. Обратитесь в поддержку, если не применится в течение часа."
        logger.error(
            "TRAFFIC_PACK_NOT_APPLIED: provider=%s tg=%s gb=%s purchase=%s — needs manual resolution",
            provider, telegram_id, traffic_gb, purchase_id,
        )

    if not rmn_success and _is_bypass:
        text += "\n\n⚠️ Активация трафика задерживается. Обратитесь в поддержку, если не применится в течение часа."

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    if _is_bypass:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👤 Личный кабинет", callback_data="menu_profile")],
            [InlineKeyboardButton(text="🌐 Купить ещё ГБ", callback_data="buy_traffic")],
            [InlineKeyboardButton(text="← На главную", callback_data="menu_main")],
        ])
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "traffic.back_to_traffic"),
                callback_data="traffic_info",
            )],
        ])
    try:
        await bot.send_message(telegram_id, text, reply_markup=kb, parse_mode="HTML")
    except Exception as send_err:
        logger.warning(
            "%s: failed to send traffic pack confirmation to user=%s: %s",
            provider, telegram_id, send_err,
        )


