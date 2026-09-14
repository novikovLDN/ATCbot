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


def _outbox_on(provider: str) -> bool:
    """T8: provisioning outbox flag for this provider's entry point
    ("telegram" for Telegram-native / Stars, else "webhook"). Env read only."""
    from app.services import provisioning_flags
    from database.subscriptions import provisioning_entrypoint
    return provisioning_flags.is_on(provisioning_entrypoint(provider))


async def _outbox_job(provider: str, purchase_id: str) -> Optional[Dict[str, Any]]:
    """T8: the provisioning job of this purchase, looked up only when the
    outbox flag is on for the provider (flag off → None, no DB read).
    A lookup error under flag ON is re-raised: guessing "legacy" could add GB twice."""
    if not _outbox_on(provider):
        return None
    import database.provisioning_jobs as provisioning_jobs
    return await provisioning_jobs.get_by_key(f"purchase:{purchase_id}")


def _pending_activation_keyboard(language: str):
    """Same buttons as the Telegram-native payment.pending_activation screen."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from app.handlers.common.emoji import CE
    from app.i18n import get_text as i18n_get_text
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.profile"),
            callback_data="menu_profile",
            icon_custom_emoji_id=CE["profile"],
            style="primary",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.support"),
            url="https://t.me/atlas_suppbot",
        )],
    ])


async def _get_current_bypass_bytes(telegram_id: int) -> Optional[int]:
    """Snapshot текущего trafficLimitBytes bypass entity перед top-up.
    Нужен verify_bypass_delivery — точно сравнить diff после add_traffic.

    Резолвим через get_bypass_entity_safe (username=str(tg)) — ТУ ЖЕ энтити,
    что патчит add_bypass_traffic. Раньше читали через remnawave_uuid, и при
    контаминации колонок baseline/add/verify расходились по разным энтити.
    """
    try:
        from app.services import remnawave_api
        entity = await remnawave_api.get_bypass_entity_safe(telegram_id)
        if not isinstance(entity, dict):
            return None
        return int(entity.get("trafficLimitBytes") or 0)
    except Exception:
        return None


async def _deliver_bypass_gb(telegram_id: int, extra_bytes: int) -> bool:
    """Начислить `extra_bytes` bypass-трафика, СОЗДАВ entity если его нет.

    Единый устойчивый примитив доставки bypass ГБ для ЛЮБОГО платежа
    (combo-подписка, обычный renewal, traffic-pack). 3 ветки:
      1. Top-up существующей bypass entity (clean primitive по numeric id).
      2. Нет по кешу → re-resolve через get_bypass_entity_safe (self-heal
         DB-указателей) и повторный top-up.
      3. Entity нет в панели вообще → create fresh с extra_bytes как
         первичным лимитом + персист uuid/id в БД.

    ⚠️ Почему это важно: раньше combo/renewal-путь звал только
    remnawave_bypass.add_bypass_traffic (top-up-only), который возвращает
    False, если у юзера ещё НЕТ bypass entity (renewal активной premium-
    подписки, у которой bypass так и не создался). Итог: срок продлевался,
    а ГБ обхода молча не начислялись. Теперь падаем на create, как это
    давно делает traffic-pack.

    Возвращает True если ГБ реально доставлены.
    """
    if extra_bytes <= 0:
        return False
    from app.services import remnawave_bypass, remnawave_api

    # Ветка 1 — top-up (entity уже есть по кешу).
    if await remnawave_bypass.add_bypass_traffic(telegram_id, extra_bytes=extra_bytes):
        return True

    # Ветка 2 — re-resolve через username (self-heal DB) + повторный top-up.
    entity = await remnawave_api.get_bypass_entity_safe(telegram_id)
    if entity is not None:
        if await remnawave_bypass.add_bypass_traffic(telegram_id, extra_bytes=extra_bytes):
            return True

    # Ветка 3 — entity в панели нет → создаём fresh с extra_bytes как лимитом.
    result_create = await remnawave_bypass.create_bypass_user_entity(
        telegram_id, traffic_limit_bytes=extra_bytes,
    )
    if result_create.ok:
        if result_create.panel_uuid:
            await database.set_remnawave_bypass_cache(
                telegram_id,
                str(result_create.panel_uuid),
                str(result_create.subscription_url) if result_create.subscription_url else None,
                str(result_create.short_uuid) if result_create.short_uuid else None,
            )
        if result_create.panel_id is not None:
            try:
                await database.set_remnawave_id(telegram_id, int(result_create.panel_id))
            except (TypeError, ValueError):
                pass
        return True
    return False


_inflight: set = set()   # strong refs: shielded confirmations outliving a webhook timeout


def _finish_inflight(task: "asyncio.Task") -> None:
    _inflight.discard(task)
    if not task.cancelled():
        task.exception()  # retrieved: every branch of the body already logs / alerts


async def process_confirmed_payment(
    provider: str,
    purchase_id: str,
    amount_rubles: float,
    invoice_id: str,
    telegram_id: int,
    bot: Bot,
) -> dict:
    """P1-1: the confirmation runs as a SHIELDED task. The webhook's
    wait_for(25 s) cancels only the wait (→ 500 + alert), never the processing
    half-way: a commit followed by a cancelled delivery could not be redone — a
    provider retry stops at already_processed. Same result / exceptions as
    _process_confirmed_payment for an uncancelled caller."""
    task = asyncio.ensure_future(_process_confirmed_payment(
        provider, purchase_id, amount_rubles, invoice_id, telegram_id, bot,
    ))
    _inflight.add(task)
    task.add_done_callback(_finish_inflight)
    return await asyncio.shield(task)


async def _process_confirmed_payment(
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
    # Bound before the try: the ValueError replay branch reads `pending`, and a
    # ValueError may be raised before the lookup below assigns it.
    pending = None
    try:
        # Check if this is a notification-only purchase (no subscription to activate).
        # Accept both 'pending' and 'expired' — user may have started a new purchase
        # flow which marked this one expired before the webhook arrived. The payment
        # itself is still valid and must not be dropped. Consistent with
        # lookup_pending_purchase() upstream and finalize_purchase()'s recovery path.
        pending = await database.get_pending_purchase_by_id(purchase_id, check_expiry=False)
        if not pending or pending.get("telegram_id") != telegram_id:
            logger.error(f"{provider} webhook: pending purchase not found: {purchase_id}")
            await _report_webhook_anomaly(
                provider, purchase_id,
                stage="confirm_purchase_not_found",
                telegram_id=telegram_id,
                message=(
                    f"{provider}: confirmed payment, but the purchase is missing or belongs "
                    f"to another user\nPurchase: {purchase_id}\nUser TG ID: {telegram_id}\n"
                    "Payment was NOT credited. Check the provider dashboard and grant "
                    "access or refund manually."
                ),
                alert=True,
            )
            return {"status": "error", "message": "Purchase not found"}

        _purchase_type = pending.get("purchase_type") or "subscription"
        _tariff = pending.get("tariff") or ""

        # Stars / Premium / Apple ID / Steam / Spotify / Proxy — just mark
        # paid + send notifications (no subscription to finalize)
        if (
            _purchase_type in ("telegram_stars", "telegram_premium", "steam", "proxy", "spotify")
            or _tariff.startswith("apple_id_")
            or _tariff.startswith("steam_")
            or _tariff.startswith("spotify_")
        ):
            marked = await database.mark_pending_purchase_paid(purchase_id)
            if not marked:
                logger.info(
                    f"{provider} webhook: {_purchase_type} already finalized (concurrent webhook), "
                    f"purchase_id={purchase_id} — skipping notification to avoid duplicate"
                )
                return {"status": "already_processed", "purchase_id": purchase_id}
            logger.info(f"{provider} webhook: {_purchase_type} marked paid, purchase_id={purchase_id}")
            # Owner rule 2026-09-14 (N17): referral cashback for ANY purchase, the
            # shop included — accrued here, in the shared core; never raises.
            await database.award_referral_cashback(
                buyer_id=telegram_id, purchase_id=purchase_id, amount_rubles=amount_rubles,
            )

            try:
                if _purchase_type == "telegram_stars":
                    from app.handlers.payments.telegram_stars_purchase import send_stars_success
                    await send_stars_success(bot, telegram_id, purchase_id, pending)
                elif _purchase_type == "telegram_premium":
                    from app.handlers.payments.telegram_premium import send_premium_success
                    await send_premium_success(bot, telegram_id, purchase_id, pending)
                elif _purchase_type == "steam" or _tariff.startswith("steam_"):
                    from app.handlers.payments.steam_purchase import send_steam_success
                    await send_steam_success(bot, telegram_id, purchase_id, pending)
                elif _purchase_type == "proxy":
                    from app.handlers.proxy import send_proxy_success
                    await send_proxy_success(bot, telegram_id, purchase_id, pending)
                elif _tariff.startswith("apple_id_"):
                    tariff_parts = _tariff.split("_")
                    region = tariff_parts[2] if len(tariff_parts) >= 3 else "usa"
                    nominal = int(tariff_parts[3]) if len(tariff_parts) >= 4 else 0
                    from app.handlers.callbacks.navigation import send_apple_id_success
                    await send_apple_id_success(bot, telegram_id, region, nominal, amount_rubles)
                elif _purchase_type == "spotify" or _tariff.startswith("spotify_"):
                    from app.handlers.payments.spotify_purchase import send_spotify_success
                    await send_spotify_success(bot, telegram_id, purchase_id, pending, provider=provider)
            except Exception as notif_err:
                # «Заказ не теряется»: already marked paid → a human must fulfil it.
                logger.error(
                    f"{provider} webhook: notification failed for {_purchase_type}: "
                    f"{type(notif_err).__name__}"
                )
                await alert_shop_order_not_notified(
                    bot,
                    stage="shop_admin_notify_failed",
                    purchase_id=purchase_id,
                    telegram_id=telegram_id,
                    provider=provider,
                    product=f"{_purchase_type} / {_tariff}",
                    amount_rubles=amount_rubles,
                    reason="send_*_success упал после mark_pending_purchase_paid",
                    error=notif_err,
                    pending=pending,
                )

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

        # Premium sync after commit failed (legacy renewal). The billing IS
        # committed: still notify the user and deliver the bypass GB (a retry
        # stops at already_processed and would never do it), THEN answer 5xx.
        # purchase_flow already alerted the admin and scheduled a re-sync.
        sync_failed_err = None
        if result.get("remnawave_sync_failed"):
            sync_failed_err = result.get("remnawave_sync_error") or "unknown"
            logger.error(
                f"WEBHOOK_RETRY_REQUESTED: provider={provider}, user={telegram_id}, "
                f"purchase_id={purchase_id}, remnawave_sync_error={sync_failed_err}"
            )

        payment_id = result["payment_id"]
        expires_at = result.get("expires_at")
        is_balance_topup = result.get("is_balance_topup", False)
        is_traffic_pack = result.get("is_traffic_pack", False)
        is_gift = result.get("is_gift", False)

        # Notification failure must NOT fail the payment — DB is already committed.
        # add_bypass_traffic имеет self-heal → в 99% случаев первый заход успешен.
        # Если всё-таки упало (сеть/panel outage) — админ увидит алерт и добавит
        # GB вручную через Traffic Audit dashboard (retry опасен: add_bypass_traffic
        # НЕ идемпотентен по purchase_id, ретрай = double-add).
        try:
            if is_gift:
                # Подарочная подписка (внешняя оплата): finalize_purchase уже
                # создал gift_code, здесь шлём покупателю share-ссылку.
                await _handle_gift_confirmation(
                    provider=provider,
                    bot=bot,
                    telegram_id=telegram_id,
                    payment_id=payment_id,
                    purchase_id=purchase_id,
                    result=result,
                )
            elif is_traffic_pack:
                await _handle_traffic_pack_confirmation(
                    provider=provider,
                    bot=bot,
                    telegram_id=telegram_id,
                    payment_id=payment_id,
                    purchase_id=purchase_id,
                    traffic_gb=result.get("traffic_gb", 0),
                    tariff_type=result.get("tariff_type", ""),
                    provisioning_job_id=result.get("provisioning_job_id"),
                    provisioning_done=result.get("provisioning_done"),
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
        except TransientPaymentError as tpe:
            # add_bypass_traffic / traffic_pack не смогли положить GB.
            # Не ронять webhook: retry делает double-add (не идемпотентен).
            # Алертнуть админа — он добавит через Traffic Audit dashboard.
            logger.error(
                f"BYPASS_GB_DELIVERY_STUCK: provider={provider} user={telegram_id} "
                f"purchase_id={purchase_id} payment_id={payment_id} err={tpe} — "
                f"нужен ручной add via Traffic Audit dashboard (retry опасен: double-add)"
            )
            try:
                from app.services.admin_alerts import alert_payment_failure
                await alert_payment_failure(
                    bot, provider, telegram_id, purchase_id, tpe,
                    is_transient=False,  # НЕ transient чтобы админ увидел и починил
                    amount_rubles=amount_rubles,
                    tariff=result.get("subscription_type") if isinstance(result, dict) else None,
                    period_days=result.get("period_days") if isinstance(result, dict) else None,
                )
            except Exception as _ae:
                logger.warning("BYPASS_GB_ALERT_FAIL: %s", _ae)
        except Exception as notif_err:
            logger.error(
                f"PAYMENT_NOTIFICATION_FAILED: provider={provider}, user={telegram_id}, "
                f"purchase_id={purchase_id}, payment_id={payment_id}, "
                f"error={type(notif_err).__name__}: {notif_err} — payment was successful"
            )
            # The same block delivers the legacy bypass GB: an unexpected error
            # here may mean GB not delivered — the admin must know (payment is
            # committed, a retry would not deliver them).
            try:
                from app.services.admin_alerts import alert_payment_failure
                await alert_payment_failure(
                    bot, provider, telegram_id, purchase_id, notif_err,
                    is_transient=False,
                    amount_rubles=amount_rubles,
                    tariff=result.get("subscription_type") if isinstance(result, dict) else None,
                    period_days=result.get("period_days") if isinstance(result, dict) else None,
                )
            except Exception as _ae:  # noqa: BLE001
                logger.warning("PAYMENT_NOTIFICATION_ALERT_FAIL: %s", _ae)

        if sync_failed_err is not None:
            # P2-26: purchase_flow.sync_renewal_to_remnawave already sent the forced
            # alert and scheduled the re-sync — one incident, one alert: neither the
            # transient branch below nor the webhook route alerts it again.
            tpe = TransientPaymentError(f"Remnawave sync failed: {sync_failed_err}")
            tpe.alerted = True
            raise tpe


    except TransientPaymentError as e:
        # Уже классифицировано как transient (например, Remnawave sync упал
        # после commit). Должно дойти до роута вебхука → HTTP 500 → провайдер
        # ретраит. До T6 его глотал общий `except Exception` ниже → 200 +
        # ложный PERMANENT-алерт.
        logger.error(
            f"PAYMENT_TRANSIENT_ERROR: provider={provider}, user={telegram_id}, "
            f"purchase_id={purchase_id}, error={type(e).__name__}: {e}"
        )
        if getattr(e, "alerted", False):
            raise  # P2-26: this incident's alert is already out
        from app.services.admin_alerts import alert_payment_failure
        tariff, period_days = await _lookup_purchase_tariff(purchase_id)
        # P1-1: `alerted` = really sent (not cut by the cooldown) → the webhook
        # route does not alert the same incident a second time.
        e.alerted = bool(await alert_payment_failure(
            bot, provider, telegram_id, purchase_id, e, is_transient=True,
            amount_rubles=amount_rubles, tariff=tariff, period_days=period_days,
        ))
        raise
    except ValueError as e:
        # P0-1: only PurchaseAlreadyProcessed is the idempotent duplicate.
        # PAYMENT_AMOUNT_MISMATCH → its own alert below; ANY other ValueError
        # (invalid status, unknown period, bad date, missing farm plot, …) is a
        # paid purchase that was NOT credited → PERMANENT forced alert +
        # payment_errors, never a silent "already processed".
        err_str = str(e)
        if isinstance(e, database.PurchaseAlreadyProcessed):
            pass  # → idempotent replay branch below
        elif not ("PAYMENT_AMOUNT_MISMATCH" in err_str or "amount mismatch" in err_str.lower()):
            logger.error(
                "PAYMENT_FINALIZE_REJECTED: provider=%s user=%s purchase_id=%s error=%s",
                provider, telegram_id, purchase_id, err_str,
            )
            await _report_webhook_anomaly(
                provider, purchase_id, stage="finalize_rejected", telegram_id=telegram_id,
                message=f"finalize_purchase ValueError: {err_str}", alert=False,
            )
            from app.services.admin_alerts import alert_payment_failure
            tariff, period_days = await _lookup_purchase_tariff(purchase_id)
            await alert_payment_failure(
                bot, provider, telegram_id, purchase_id, e, is_transient=False,
                amount_rubles=amount_rubles, tariff=tariff, period_days=period_days,
            )
            return {"status": "error"}
        if "PAYMENT_AMOUNT_MISMATCH" in err_str or "amount mismatch" in err_str.lower():
            logger.error(
                "PAYMENT_MISMATCH_UNRECOVERABLE: provider=%s user=%s purchase_id=%s error=%s",
                provider, telegram_id, purchase_id, err_str,
            )
            # Уведомить админа — юзер оплатил, но mismatch мешает финализации.
            # Нужно вручную либо активировать подписку, либо вернуть деньги.
            try:
                import admin_notifications as _an
                admin_text = (
                    f"⚠️ <b>Payment amount mismatch (unrecovered)</b>\n\n"
                    f"Provider: <code>{provider}</code>\n"
                    f"User: <code>{telegram_id}</code>\n"
                    f"Purchase: <code>{purchase_id}</code>\n"
                    f"Webhook amount: <b>{amount_rubles:.2f} ₽</b>\n\n"
                    f"<b>Error:</b>\n<code>{err_str[:400]}</code>\n\n"
                    f"Юзер оплатил, но подписка не активировалась. "
                    f"Нужно либо активировать вручную, либо вернуть деньги."
                )
                await _an.send_admin_notification(
                    bot=bot, message=admin_text,
                    notification_type="payment_amount_mismatch",
                    parse_mode="HTML",
                )
            except Exception as notify_err:
                logger.warning("PAYMENT_MISMATCH_ADMIN_NOTIFY_FAILED: %s", notify_err)
            # Возвращаем error чтобы провайдер НЕ считал успешным
            # (не ретраил зря, но и не забыл).
            return {"status": "amount_mismatch", "error": err_str[:200]}

        logger.info(
            f"{provider} webhook: purchase already processed (ValueError): "
            f"purchase_id={purchase_id}, error={e}"
        )
        # Provider retry path: the first webhook committed the DB, but the
        # post-commit Remnawave sync may have failed. Re-run the idempotent
        # provision so the user lands in sync. provision_subscription handles
        # both create and renew, and adopts existing panel entities.
        # Only run for an actually-active subscription whose row already has
        # a future expires_at — never resync something we deliberately let
        # expire.
        try:
            # T8: purchase finalized through the provisioning outbox → the
            # replay only nudges its job (never provision_subscription): not
            # done → run_now (never raises), done → nothing. No job (finalized
            # by the legacy path, or not an access-granting kind) → legacy below.
            job = await _outbox_job(provider, purchase_id)
            if job is not None:
                if job.get("status") != "done":
                    from app.services import provisioning
                    await provisioning.run_now(int(job["id"]), bot=bot)
                    logger.info(
                        "WEBHOOK_REPLAY_PROVISIONING_RUN_NOW: provider=%s user=%s purchase_id=%s "
                        "job=%s status=%s", provider, telegram_id, purchase_id, job["id"], job.get("status"),
                    )
                else:
                    logger.info(
                        "WEBHOOK_REPLAY_PROVISIONING_DONE: provider=%s user=%s purchase_id=%s job=%s",
                        provider, telegram_id, purchase_id, job["id"],
                    )
                return {"status": "already_processed"}
            from app.services import purchase_flow
            from datetime import datetime, timezone
            sub = await database.get_subscription(telegram_id)
            sub_expires = sub.get("expires_at") if sub else None
            sub_tariff = sub.get("subscription_type") if sub else None
            # ВАЖНО: bypass-only строки НЕ ресинкать через provision_subscription.
            # У них subscription_type='basic' и expires_at=NOW+10y по дизайну
            # ensure_bypass_only_subscription, но реальной премиум-подписки
            # нет — ретрай webhook'а на traffic-pack раньше создавал
            # фантомный `tg_<id>_premium` в панели с expireAt=+10y. Юзер,
            # купивший только 15 ГБ трафика, получал безлимитный premium-
            # доступ на 10 лет. Bypass-энтити создаётся отдельно в
            # _handle_traffic_pack_confirmation — второй webhook просто
            # ничего не делать не должен.
            is_bypass_only = bool(sub.get("is_bypass_only")) if sub else False
            still_active = bool(
                sub_expires
                and sub_tariff
                and sub_expires > datetime.now(timezone.utc)
                and not is_bypass_only
            )
            if still_active:
                _pd = (pending.get("period_days") if pending else None) or 30
                await purchase_flow.provision_subscription(
                    telegram_id,
                    tariff=sub_tariff,
                    subscription_end=sub_expires,
                    period_days=int(_pd),
                    is_trial=False,
                )
                logger.info(
                    f"WEBHOOK_REPLAY_RESYNCED: provider={provider}, user={telegram_id}, "
                    f"purchase_id={purchase_id}"
                )
            elif is_bypass_only:
                logger.info(
                    "WEBHOOK_REPLAY_SKIPPED_BYPASS_ONLY: provider=%s, user=%s, "
                    "purchase_id=%s — bypass-only row, no premium resync needed "
                    "(traffic-pack handler already added the GB to Remnawave)",
                    provider, telegram_id, purchase_id,
                )
        except Exception as resync_err:
            logger.error(
                f"WEBHOOK_REPLAY_RESYNC_FAILED: provider={provider}, user={telegram_id}, "
                f"purchase_id={purchase_id}, error={resync_err}"
            )
            tpe = TransientPaymentError(f"Replay resync to Remnawave failed: {resync_err}")
            # P2-26: a provider retry while the panel is still down — the premium
            # problem of this user was already alerted (sync failure + re-sync).
            from app.services.payments import verify_delivery
            tpe.alerted = verify_delivery.recently_alerted(telegram_id, "premium")
            raise tpe from resync_err
        return {"status": "already_processed"}
    except (asyncpg.PostgresError, asyncio.TimeoutError, OSError, RuntimeError) as e:
        # Transient infrastructure error (DB / network / Remnawave provision
        # raised RuntimeError) — provider MUST retry. provision_subscription
        # raises RuntimeError when the panel responds non-2xx; treat as transient
        # so the webhook returns 5xx and the payment provider replays it.
        logger.error(
            f"PAYMENT_TRANSIENT_ERROR: provider={provider}, user={telegram_id}, "
            f"purchase_id={purchase_id}, error={type(e).__name__}: {e}"
        )
        from app.services.admin_alerts import alert_payment_failure
        tariff, period_days = await _lookup_purchase_tariff(purchase_id)
        alerted = bool(await alert_payment_failure(
            bot, provider, telegram_id, purchase_id, e, is_transient=True,
            amount_rubles=amount_rubles, tariff=tariff, period_days=period_days,
        ))
        tpe = TransientPaymentError(f"Transient error during payment: {type(e).__name__}: {e}")
        tpe.alerted = alerted
        raise tpe from e
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

    Fetches ANY status so we can distinguish an idempotent webhook retry
    (row exists with status='paid') from a truly missing row (data loss
    or an orphaned provider invoice pointing at a purchase_id we never
    persisted — the latter needs manual admin attention).

    Provider check (T6): if the row has `payment_provider` set and it differs
    from the webhook's provider → {"status": "provider_mismatch"} + forced
    admin alert. NULL (legacy rows, most shop rows) is accepted and logged.
    Shop/notification-only purchases are never rejected by this check.

    Returns:
        {"status": "ok", "purchase": dict, "telegram_id": int} on success
        {"status": "not_found"|"already_processed"|"invalid_status"|
         "provider_mismatch"} on failure
    """
    pending_purchase = await database.get_pending_purchase_any_status(purchase_id)

    if not pending_purchase:
        logger.error(
            f"{provider} webhook: purchase not found in DB: purchase_id={purchase_id} — "
            "row missing entirely, payment cannot be reconciled automatically"
        )
        # 200 stays (a retry will not create the row), but a human must look.
        # WATA sends its own forced orphan alert (with tx id / amount) in
        # wata_service — don't double-alert it here.
        await _report_webhook_anomaly(
            provider, purchase_id,
            stage="webhook_purchase_not_found",
            message=(
                f"{provider}: paid webhook for a purchase that is NOT in the DB\n"
                f"Purchase: {purchase_id}\n"
                "Payment was NOT credited. Find the transaction in the provider "
                "dashboard and grant access or refund manually."
            ),
            alert=(provider != "wata"),
        )
        return {"status": "not_found"}

    telegram_id = pending_purchase["telegram_id"]
    purchase_status = pending_purchase.get("status")

    if purchase_status == "paid":
        logger.info(
            f"{provider} webhook: purchase already processed (idempotent retry): "
            f"purchase_id={purchase_id}, user={telegram_id}"
        )
        return {"status": "already_processed"}

    if purchase_status not in ("pending", "expired"):
        logger.warning(
            f"{provider} webhook: unexpected purchase status: "
            f"purchase_id={purchase_id}, status={purchase_status}"
        )
        return {"status": "invalid_status"}

    stored_provider = str(pending_purchase.get("payment_provider") or "").strip()
    if not _is_notification_only_purchase(pending_purchase):
        if stored_provider and stored_provider != provider:
            logger.error(
                "PAYMENT_PROVIDER_MISMATCH: webhook_provider=%s stored_provider=%s "
                "purchase_id=%s user=%s — rejected, not credited",
                provider, stored_provider, purchase_id, telegram_id,
            )
            await _report_webhook_anomaly(
                provider, purchase_id,
                stage="webhook_provider_mismatch",
                telegram_id=telegram_id,
                message=(
                    f"Webhook from {provider} for a purchase issued via {stored_provider}\n"
                    f"Purchase: {purchase_id}\n"
                    f"User TG ID: {telegram_id}\n"
                    "Payment was NOT credited. Check both provider dashboards and "
                    "grant access or refund manually."
                ),
                alert=True,
            )
            return {"status": "provider_mismatch", "purchase_id": purchase_id}
        if not stored_provider:
            logger.info(
                "%s webhook: purchase has no payment_provider (legacy row) — accepted, "
                "purchase_id=%s", provider, purchase_id,
            )

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


def _is_notification_only_purchase(pending: Dict[str, Any]) -> bool:
    """Same predicate as the shop/notification-only branch of
    process_confirmed_payment (mark_pending_purchase_paid + send_*_success).
    Such purchases keep their historical path: no provider check (SCOPE.md)."""
    purchase_type = pending.get("purchase_type") or "subscription"
    tariff = pending.get("tariff") or ""
    return (
        purchase_type in ("telegram_stars", "telegram_premium", "steam", "proxy", "spotify")
        or tariff.startswith("apple_id_")
        or tariff.startswith("steam_")
        or tariff.startswith("spotify_")
    )


def _webhook_bot() -> Optional[Bot]:
    """Bot stored by the webhook router at startup (payment_webhook.setup)."""
    try:
        from app.api import payment_webhook
        return payment_webhook._bot
    except Exception:  # noqa: BLE001
        return None


async def _report_webhook_anomaly(
    provider: str,
    purchase_id: str,
    *,
    stage: str,
    message: str,
    telegram_id: Optional[int] = None,
    alert: bool = True,
) -> None:
    """payment_errors row + (optionally) forced admin alert. Best-effort:
    never raises, the webhook answer must not depend on it."""
    try:
        await database.log_payment_error(
            stage=stage,
            telegram_id=telegram_id,
            purchase_id=purchase_id,
            payment_provider=provider,
            error_message=message[:500],
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("payment_errors log skipped (%s): %s", stage, e)
    if not alert:
        return
    bot = _webhook_bot()
    if bot is None:
        logger.error("%s: admin alert not sent (bot not initialised): %s", stage, message)
        return
    try:
        from app.services.admin_alerts import send_alert
        await send_alert(bot, "payment", message, force=True)
    except Exception as e:  # noqa: BLE001
        logger.error("%s: admin alert failed: %s", stage, e)


SHOP_ORDER_NOT_NOTIFIED = "Заказ оплачен, но уведомление админу не доставлено — выполните вручную"


def scrub_shop_secrets(text: str, pending: Optional[Dict[str, Any]]) -> str:
    """Shop rows keep buyer credentials in pending_purchases: Spotify password in
    promo_code, email in country. Mask them in any text that goes to logs,
    alerts or payment_errors."""
    text = str(text)
    if not pending:
        return text
    for secret in (pending.get("promo_code"), pending.get("country")):
        secret = str(secret or "").strip()
        if len(secret) >= 3:
            text = text.replace(secret, "***")
    return text


async def alert_shop_order_not_notified(
    bot: Optional[Bot],
    *,
    stage: str,
    purchase_id: Optional[str],
    telegram_id: int,
    provider: Optional[str],
    product: str,
    amount_rubles: Optional[float] = None,
    username: Optional[str] = None,
    reason: str = "",
    error: Optional[BaseException] = None,
    pending: Optional[Dict[str, Any]] = None,
) -> bool:
    """«Заказ не теряется»: a PAID shop order whose admin notification did not
    go through → payment_errors row + separate FORCED admin alert (short, plain
    text, never the buyer's password/email). Alert failed too → CRITICAL log.
    Never raises. Returns True if the alert was delivered."""
    if username is None:
        try:
            user = await database.get_user(telegram_id)
            username = f"@{user['username']}" if user and user.get("username") else "—"
        except Exception:  # noqa: BLE001
            username = "—"
    lines = [
        SHOP_ORDER_NOT_NOTIFIED,
        "",
        f"Заказ (purchase_id): {purchase_id}",
        f"Товар: {product}",
        f"Покупатель TG ID: {telegram_id}",
        f"Username: {username}",
    ]
    if amount_rubles is not None:
        lines.append(f"Сумма: {amount_rubles} ₽")
    lines.append(f"Провайдер: {provider or '—'}")
    if reason:
        lines.append(f"Причина: {reason}")
    if error is not None:
        # Only the exception TYPE: its message may embed buyer credentials in a
        # form scrub_shop_secrets can't match (truncated, escaped, encoded).
        lines.append(f"Ошибка: {type(error).__name__}")
    text = scrub_shop_secrets("\n".join(lines), pending)

    try:
        await database.log_payment_error(
            stage=stage,
            telegram_id=telegram_id,
            purchase_id=purchase_id,
            payment_provider=provider,
            amount_rubles=amount_rubles,
            error_code=type(error).__name__ if error is not None else None,
            error_message=text[:500],
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("payment_errors log skipped (%s): %s", stage, type(e).__name__)

    sent = False
    if bot is not None:
        try:
            from app.services import admin_alerts
            sent = bool(await admin_alerts.send_alert(bot, "payment", text, force=True))
        except Exception as e:  # noqa: BLE001
            logger.error(
                "SHOP_ORDER_ALERT_ERROR stage=%s purchase_id=%s: %s",
                stage, purchase_id, type(e).__name__,
            )
    if not sent:
        logger.critical(
            "SHOP_ORDER_LOST_RISK stage=%s purchase_id=%s user=%s username=%s provider=%s "
            "product=%s amount=%s — paid order, admin NOT notified, fulfil manually",
            stage, purchase_id, telegram_id, username, provider, product, amount_rubles,
        )
    return sent


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

    # Идемпотентность: mark-before-send через payment_notifications_sent.
    # finalize_purchase уже гарантирует single-writer через advisory-lock
    # по purchase_id + FOR UPDATE внутри tx — но на нём защита СТАТУСА покупки, а не факта отправки
    # уведомления. Если между finalize и send прилетит другой путь
    # (fast-poll + webhook, reconciler + webhook, кнопка «Проверить» +
    # webhook) — второй пропустится сразу. Даёт двойной страховщик поверх
    # DB-lock и гасит любые оставшиеся гонки.
    try:
        sent = await database.mark_payment_notification_sent(payment_id)
    except Exception as _flag_err:  # noqa: BLE001
        logger.warning(
            "notification_flag_check_failed provider=%s user=%s payment_id=%s err=%s — "
            "продолжаем отправку (fail-open)",
            provider, telegram_id, payment_id, _flag_err,
        )
        sent = True
    if not sent:
        logger.info(
            "NOTIFICATION_IDEMPOTENT_SKIP: provider=%s user=%s payment_id=%s purchase_id=%s "
            "— повторное подтверждение подавлено",
            provider, telegram_id, payment_id, purchase_id,
        )
        return

    # Убираем экран «Ждём платёж» перед отправкой подтверждения — иначе
    # юзер видит одновременно устаревший invoice и «✅ Платёж успешно
    # обработан».  Best-effort, никаких await на удаление.
    try:
        from app.handlers.callbacks.payments_callbacks import delete_invoice_message_for_purchase
        await delete_invoice_message_for_purchase(bot, purchase_id)
    except Exception as _e:  # noqa: BLE001
        logger.debug("invoice_screen_cleanup skipped: %s", _e)

    from app.services.payments.success_message import build_purchase_success, build_topup_success
    from app.utils.telegram_safe import safe_send_message

    if is_balance_topup:
        topup_amount = result.get("amount", amount_rubles)
        try:
            new_balance = await database.get_user_balance(telegram_id)
        except Exception:  # noqa: BLE001 — the credited amount is still shown
            new_balance = None
        text, reply_markup = build_topup_success(language, amount=topup_amount, balance=new_balance)
        try:
            await safe_send_message(bot, telegram_id, text, reply_markup=reply_markup, parse_mode="HTML")
        except Exception as send_err:
            logger.warning(
                f"{provider}: failed to send topup confirmation to user={telegram_id}: {send_err}"
            )
        logger.info(
            f"{provider} payment processed (balance topup): user={telegram_id}, "
            f"payment_id={payment_id}, amount={topup_amount} RUB"
        )
    else:
        from app.utils.date_utils import format_date_msk
        expires_str = format_date_msk(expires_at) if expires_at else "N/A"
        subscription_type = (result.get("subscription_type") or "basic").strip().lower()
        if subscription_type not in config.VALID_SUBSCRIPTION_TYPES:
            subscription_type = "basic"

        # T8: finalized through the provisioning outbox → GB/premium are the
        # job's; a new issuance whose job did not finish yet (run_now failed,
        # worker retries) shows payment.pending_activation instead of links.
        via_outbox = result.get("provisioning_job_id") is not None
        outbox_pending = (
            via_outbox
            and result.get("activation_status") == "pending"
            and not result.get("is_renewal")
        )
        if not via_outbox and not result.get("is_renewal"):
            # Legacy first purchase: delayed DB↔panel check (renewals with GB are
            # verified below by verify_premium/bypass_delivery).
            from app.services.payments import verify_delivery
            verify_delivery.schedule_legacy_check(
                telegram_id, source="webhook", ref=str(purchase_id),
                expect_bypass=True,
            )
        if outbox_pending:
            text = i18n_get_text(language, "payment.pending_activation", date=expires_str)
            reply_markup = _pending_activation_keyboard(language)
        else:
            # One success message for every payment path (08_payments_ux #3):
            # tariff incl. Combo, period, new end date, GB added, connect keyboard
            # in the user's language.
            text, reply_markup = await build_purchase_success(
                language,
                subscription_type=subscription_type,
                is_combo=bool(result.get("is_combo")),
                period_days=result.get("period_days"),
                expires_at=expires_at,
                is_renewal=bool(result.get("is_renewal")),
                is_upgrade=bool(result.get("is_basic_to_plus_upgrade")),
                telegram_id=telegram_id,
            )

        try:
            await safe_send_message(
                bot, telegram_id, text, reply_markup=reply_markup, parse_mode="HTML"
            )
        except Exception as send_err:
            logger.warning(
                f"{provider}: failed to send subscription confirmation to user={telegram_id}: {send_err}"
            )

        logger.info(
            f"{provider} payment processed: user={telegram_id}, payment_id={payment_id}, "
            f"purchase_id={purchase_id}, subscription_activated=True"
        )

        if via_outbox:
            logger.info(
                "BYPASS_GB_VIA_OUTBOX: provider=%s user=%s purchase_id=%s job=%s done=%s pending=%s",
                provider, telegram_id, purchase_id, result.get("provisioning_job_id"),
                result.get("provisioning_done"), outbox_pending,
            )
            return

        # ── Bypass GB accumulation ─────────────────────────────────────
        # Единая точка добавления bypass GB (combo и обычная подписка).
        # sync_renewal_to_remnawave теперь ТОЛЬКО продлевает premium.expireAt
        # (см. purchase_flow.py) — bypass GB кладём здесь, ровно сколько
        # положено по тарифу, без дублей.
        #
        # Правила:
        #   combo_basic / combo_plus   → COMBO_TARIFFS[key][period]["gb"] GB
        #   basic / plus (обычные)     → TRAFFIC_LIMITS[tariff][period] bytes
        #   trial / telegram_*         → skip (не имеют bypass ГБ по ТЗ)
        #
        # ВАЖНО: если bypass entity ТОЛЬКО ЧТО создан (fresh) — provision уже
        # выставил ему финальный лимит (75 GB combo или 10 GB basic 30d).
        # Здесь пропускаем top-up, иначе double-add → 150 GB для fresh combo.
        # Для renewal (entity уже был) — top-up здесь единственный источник GB.
        is_combo = result.get("is_combo", False)
        bypass_created_fresh = result.get("bypass_created_fresh", False)
        _skip_bypass = (
            not expires_at
            or subscription_type in ("trial", "telegram_premium", "telegram_stars")
            or bypass_created_fresh  # fresh entity → уже с финальным лимитом
        )
        if bypass_created_fresh and not _skip_bypass:
            # Не должно случиться (fresh уже в _skip_bypass) — защита от рефакторингов.
            _skip_bypass = True
        if bypass_created_fresh:
            logger.info(
                "BYPASS_TOPUP_SKIPPED_FRESH_ENTITY: provider=%s user=%s tariff=%s "
                "is_combo=%s — bypass entity создан с финальным лимитом в provision_subscription",
                provider, telegram_id, subscription_type, is_combo,
            )
            # Для combo всё равно записываем в traffic_purchases (для Traffic Audit).
            if is_combo:
                try:
                    _pd_combo = result.get("period_days", 30) or 30
                    _combo_key = f"combo_{subscription_type}"
                    _combo_info = config.COMBO_TARIFFS.get(_combo_key, {}).get(_pd_combo)
                    if _combo_info:
                        await database.record_traffic_purchase(
                            telegram_id, int(_combo_info["gb"]), 0,
                        )
                except Exception as _rp_err:
                    logger.warning(
                        "record_traffic_purchase (fresh combo) failed user=%s: %s",
                        telegram_id, _rp_err,
                    )
        if not _skip_bypass:
            _pd = result.get("period_days", 30) or 30
            gb_to_add = 0
            tariff_label = subscription_type
            if is_combo:
                combo_key = f"combo_{subscription_type}"
                combo_info = config.COMBO_TARIFFS.get(combo_key, {}).get(_pd)
                if not combo_info:
                    logger.error(
                        "COMBO_TARIFF_NOT_FOUND: provider=%s user=%s combo_key=%s period=%s",
                        provider, telegram_id, combo_key, _pd,
                    )
                    raise TransientPaymentError(
                        f"combo tariff config missing: {combo_key}/{_pd}d"
                    )
                gb_to_add = int(combo_info["gb"])
                tariff_label = combo_key
            else:
                # Обычная basic/plus подписка: TRAFFIC_LIMITS уже в bytes.
                table = config.TRAFFIC_LIMITS.get(subscription_type, {})
                if isinstance(table, dict) and _pd in table:
                    gb_to_add = int(table[_pd]) // (1024 ** 3)
                elif isinstance(table, dict) and table:
                    # Ближайший период (для нестандартных pd).
                    gb_to_add = int(table[max(k for k in table.keys() if k <= _pd)
                                        if any(k <= _pd for k in table.keys())
                                        else min(table.keys())]) // (1024 ** 3)
            if gb_to_add > 0:
                traffic_bytes = gb_to_add * (1024 ** 3)
                baseline_bytes = await _get_current_bypass_bytes(telegram_id)
                # Устойчивая доставка: top-up ИЛИ create-if-missing.
                # На renewal активной premium-подписки без bypass entity
                # старый top-up-only молча терял ГБ (срок продлевался, ГБ нет).
                ok = await _deliver_bypass_gb(telegram_id, traffic_bytes)
                if not ok:
                    logger.error(
                        "BYPASS_TRAFFIC_FAIL: provider=%s user=%s gb=%s is_combo=%s — retry",
                        provider, telegram_id, gb_to_add, is_combo,
                    )
                    raise TransientPaymentError(
                        f"bypass-traffic add failed: user={telegram_id} gb={gb_to_add} combo={is_combo}"
                    )
                if is_combo:
                    # Combo → в traffic_purchases (для Traffic Audit sum).
                    await database.record_traffic_purchase(telegram_id, gb_to_add, 0)
                logger.info(
                    "BYPASS_TRAFFIC_ADDED: provider=%s user=%s gb=%s tariff=%s is_combo=%s",
                    provider, telegram_id, gb_to_add, tariff_label, is_combo,
                )
                # Verify реально ли долетело — fire-and-forget.
                try:
                    from app.services.payments.verify_delivery import (
                        verify_bypass_delivery, verify_premium_delivery,
                    )
                    asyncio.create_task(verify_bypass_delivery(
                        telegram_id=telegram_id, provider=provider,
                        kind="combo" if is_combo else "renewal",
                        expected_added_bytes=traffic_bytes,
                        baseline_bytes=baseline_bytes,
                        purchase_id=str(purchase_id), tariff=tariff_label,
                        period_days=_pd,
                    ))
                    if result.get("is_renewal") and not result.get("remnawave_sync_failed"):
                        # P2-26: a failed sync was already alerted (+ re-sync scheduled).
                        # P2-27: a first purchase's premium is verified by the legacy
                        # delayed check scheduled above — one verifier per entity.
                        asyncio.create_task(verify_premium_delivery(
                            telegram_id=telegram_id, provider=provider,
                            expected_expire_at=expires_at,
                            purchase_id=str(purchase_id), tariff=tariff_label,
                            period_days=_pd,
                        ))
                except Exception:
                    pass


async def _handle_gift_confirmation(
    provider: str,
    bot: Bot,
    telegram_id: int,
    payment_id: int,
    purchase_id: str,
    result: dict,
) -> None:
    """Доставка подарочной подписки покупателю после ВНЕШНЕЙ оплаты.

    finalize_purchase уже атомарно создал строку gift_subscriptions + gift_code
    (result['is_gift']=True). Здесь идемпотентно отправляем покупателю экран с
    share-ссылкой t.me/<bot>?start=gift_<code>, чтобы он переслал подарок.

    Telegram-native путь (Stars/Payments) делает то же в
    payments_messages.py::process_successful_payment — этот хелпер закрывает
    внешних провайдеров (platega/cryptobot/wata), для которых confirmation.py
    раньше слал обычное «подписка активирована» и терял ссылку-подарок.

    Идемпотентность: mark_payment_notification_sent (как в _send_confirmation) —
    повторный вебхук не задваивает сообщение. Если отправка упадёт — код уже в
    БД, покупатель достанет ссылку через «Мои подарки».
    """
    gift_code = result.get("gift_code")
    if not gift_code:
        logger.error(
            "GIFT_CONFIRMATION_NO_CODE: provider=%s user=%s purchase_id=%s",
            provider, telegram_id, purchase_id,
        )
        return

    try:
        sent = await database.mark_payment_notification_sent(payment_id)
    except Exception as _flag_err:  # noqa: BLE001
        logger.warning(
            "GIFT_NOTIFICATION_FLAG_FAIL provider=%s user=%s payment_id=%s err=%s — fail-open",
            provider, telegram_id, payment_id, _flag_err,
        )
        sent = True
    if not sent:
        logger.info(
            "GIFT_NOTIFICATION_IDEMPOTENT_SKIP: provider=%s user=%s purchase_id=%s",
            provider, telegram_id, purchase_id,
        )
        return

    # Убрать экран «ждём оплату» перед отправкой подтверждения-подарка.
    try:
        from app.handlers.callbacks.payments_callbacks import delete_invoice_message_for_purchase
        await delete_invoice_message_for_purchase(bot, purchase_id)
    except Exception:  # noqa: BLE001
        pass

    from app.services.language_service import resolve_user_language
    language = await resolve_user_language(telegram_id)
    from app.handlers.callbacks.gift import _send_gift_success
    await _send_gift_success(
        bot=bot,
        telegram_id=telegram_id,
        language=language,
        gift_code=gift_code,
        tariff=result.get("gift_tariff") or "basic",
        period_days=int(result.get("gift_period_days") or 30),
    )
    logger.info(
        "GIFT_PAYMENT_FINALIZED: provider=%s user=%s purchase_id=%s code=%s",
        provider, telegram_id, purchase_id, gift_code,
    )


async def _handle_traffic_pack_confirmation(
    provider: str,
    bot: Bot,
    telegram_id: int,
    payment_id: int,
    purchase_id: str,
    traffic_gb: int,
    tariff_type: str = "",
    provisioning_job_id: Optional[int] = None,
    provisioning_done: Optional[bool] = None,
) -> None:
    """Send traffic pack purchase confirmation and add traffic via Remnawave.

    T8: when the purchase went through the provisioning outbox (job id passed
    by the webhook path, or found by key when the flag is on — the
    Telegram-native caller passes none) the GB are the job's: no delivery
    here, only the notification (same i18n keys)."""
    from app.services.language_service import resolve_user_language
    from app.i18n import get_text as i18n_get_text

    if provisioning_job_id is None:
        _job = await _outbox_job(provider, purchase_id)
        if _job is not None:
            provisioning_job_id = _job["id"]
            provisioning_done = _job.get("status") == "done"

    language = await resolve_user_language(telegram_id)
    _is_bypass = bool(tariff_type and tariff_type.startswith("bypass_"))

    # Bypass-only: ensure subscription row + Remnawave user exist
    if _is_bypass:
        await database.ensure_bypass_only_subscription(telegram_id)

    # Add traffic via Remnawave — clean primitive через numeric bypass id.
    # Если entity нет вообще (первый bypass-buy без подписки) — создаём.
    rmn_success = False
    _delivery_error: Optional[TransientPaymentError] = None
    pack = config.TRAFFIC_PACKS.get(traffic_gb) or config.TRAFFIC_PACKS_EXTENDED.get(traffic_gb)
    if provisioning_job_id is not None:
        rmn_success = bool(provisioning_done)
        logger.info(
            "TRAFFIC_PACK_VIA_OUTBOX provider=%s user=%s gb=%s purchase=%s job=%s done=%s",
            provider, telegram_id, traffic_gb, purchase_id, provisioning_job_id, provisioning_done,
        )
    elif pack:
        traffic_bytes = pack["bytes"]
        try:
            baseline_bytes = await _get_current_bypass_bytes(telegram_id)
            # Устойчивая доставка: top-up → self-heal → create-if-missing.
            rmn_success = await _deliver_bypass_gb(telegram_id, traffic_bytes)
            if rmn_success:
                logger.info(
                    "BYPASS_REMNAWAVE_TRAFFIC_ADDED provider=%s user=%s gb=%s",
                    provider, telegram_id, traffic_gb,
                )
            else:
                logger.error(
                    "BYPASS_TRAFFIC_ADD_FAILED provider=%s user=%s gb=%s — "
                    "все 3 ветки не помогли (top-up / self-heal / create)",
                    provider, telegram_id, traffic_gb,
                )
                raise TransientPaymentError(
                    f"traffic_pack bypass add failed: user={telegram_id} gb={traffic_gb}"
                )
            # Verify реального применения в панели (fire-and-forget).
            try:
                from app.services.payments.verify_delivery import verify_bypass_delivery
                asyncio.create_task(verify_bypass_delivery(
                    telegram_id=telegram_id, provider=provider,
                    kind="traffic_pack",
                    expected_added_bytes=traffic_bytes,
                    baseline_bytes=baseline_bytes,
                    purchase_id=str(purchase_id),
                    tariff=f"pack_{traffic_gb}gb",
                ))
            except Exception:
                pass
        except TransientPaymentError as tpe:
            if not _is_bypass:
                raise
            # Bypass-only: the gift and the user message still go out (the GB
            # line says "delayed"); raised at the end → the same admin alert.
            _delivery_error = tpe
        except Exception as rmn_err:
            logger.error(
                "TRAFFIC_PACK_REMNAWAVE_ERROR: provider=%s tg=%s gb=%s error=%s",
                provider, telegram_id, traffic_gb, rmn_err,
            )
            try:
                from app.services.payments.verify_delivery import _send_admin_alert
                asyncio.create_task(_send_admin_alert(
                    "Traffic pack: Remnawave EXCEPTION",
                    (
                        f"User: <code>tg:{telegram_id}</code>\n"
                        f"Provider: <b>{provider}</b> · Pack: <b>{traffic_gb} GB</b>\n"
                        f"Purchase: <code>{purchase_id}</code>\n"
                        f"Error: <code>{type(rmn_err).__name__}: {str(rmn_err)[:150]}</code>"
                    ),
                ))
            except Exception:
                pass
    else:
        logger.error(
            "TRAFFIC_PACK_INVALID_GB: provider=%s tg=%s gb=%s purchase=%s — pack not found in config",
            provider, telegram_id, traffic_gb, purchase_id,
        )

    # Bypass-only GB purchase without a subscription (owner, 2026-09-14,
    # docs/audit/SCOPE.md): the purchased GB + the trial's 3 days of premium as a
    # ONE-TIME gift — regardless of the "trial" flag, without the trial's 500 MB.
    # After the purchase commit; never raises (failure → forced alert + background
    # retry). Outbox when the pack went through it (the gift job then queues behind
    # the pack job, never racing it) or when the "trial" entry point is on.
    _gift = None
    if _is_bypass:
        from app.services import provisioning_flags
        from app.services.trials import service as trial_service
        _gift = await trial_service.grant_bypass_purchase_gift(
            telegram_id, bot=bot, where=f"traffic_pack:{provider}:{purchase_id}",
            via_outbox=provisioning_job_id is not None or provisioning_flags.is_on("trial"),
        )
        if _gift is not None:
            logger.info(
                "BYPASS_GIFT_GRANTED provider=%s user=%s premium_until=%s",
                provider, telegram_id, _gift.subscription_end.isoformat(),
            )

    if _is_bypass:
        text = i18n_get_text(language, "bypass.purchase_success", gb=traffic_gb)
        if not rmn_success:
            text += "\n\n" + i18n_get_text(language, "bypass.activation_delayed")
        if _gift is not None:
            text += "\n\n" + i18n_get_text(
                language, "bypass.gift_premium_granted",
                until=trial_service.format_gift_until(_gift.subscription_end),
            )
    elif rmn_success:
        text = i18n_get_text(language, "traffic.purchase_success", gb=traffic_gb, price="")
    else:
        text = i18n_get_text(language, "traffic.purchase_success", gb=traffic_gb, price="")
        text += "\n\n⚠️ Активация трафика задерживается. Обратитесь в поддержку, если не применится в течение часа."
        if provisioning_job_id is None:  # outbox: the job retries and alerts itself
            logger.error(
                "TRAFFIC_PACK_NOT_APPLIED: provider=%s tg=%s gb=%s purchase=%s — needs manual resolution",
                provider, telegram_id, traffic_gb, purchase_id,
            )

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    if _is_bypass:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "bypass.btn_profile"), callback_data="menu_profile")],
            [InlineKeyboardButton(text=i18n_get_text(language, "bypass.btn_buy_more_gb"), callback_data="buy_traffic")],
            [InlineKeyboardButton(text=i18n_get_text(language, "bypass.btn_main_menu"), callback_data="menu_main")],
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
    if _delivery_error is not None:
        raise _delivery_error  # legacy GB not delivered → the caller's forced admin alert


