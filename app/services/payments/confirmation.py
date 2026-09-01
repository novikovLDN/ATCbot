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
                    await send_spotify_success(bot, telegram_id, purchase_id, pending)
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

        if result.get("remnawave_sync_failed"):
            err = result.get("remnawave_sync_error") or "unknown"
            logger.error(
                f"WEBHOOK_RETRY_REQUESTED: provider={provider}, user={telegram_id}, "
                f"purchase_id={purchase_id}, remnawave_sync_error={err}"
            )
            raise TransientPaymentError(f"Remnawave sync failed: {err}")

        payment_id = result["payment_id"]
        expires_at = result.get("expires_at")
        is_balance_topup = result.get("is_balance_topup", False)
        is_traffic_pack = result.get("is_traffic_pack", False)

        # Notification failure must NOT fail the payment — DB is already committed.
        # add_bypass_traffic имеет self-heal → в 99% случаев первый заход успешен.
        # Если всё-таки упало (сеть/panel outage) — админ увидит алерт и добавит
        # GB вручную через Traffic Audit dashboard (retry опасен: add_bypass_traffic
        # НЕ идемпотентен по purchase_id, ретрай = double-add).
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
        # finalize_purchase кидает ValueError для ДВУХ разных случаев:
        #  1. "already processed" — идемпотентный дубль webhook'а
        #  2. "PAYMENT_AMOUNT_MISMATCH" — реальная ошибка, платёж НЕ обработан
        # До 2026-08 оба обрабатывались одинаково — mismatch тихо шёл в лог
        # как "already processed" без алерта админу. Теперь разделяем.
        err_str = str(e)
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
            raise TransientPaymentError(
                f"Replay resync to Remnawave failed: {resync_err}"
            ) from resync_err
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
        await alert_payment_failure(
            bot, provider, telegram_id, purchase_id, e, is_transient=True,
            amount_rubles=amount_rubles, tariff=tariff, period_days=period_days,
        )
        raise TransientPaymentError(
            f"Transient error during payment: {type(e).__name__}: {e}"
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

    Fetches ANY status so we can distinguish an idempotent webhook retry
    (row exists with status='paid') from a truly missing row (data loss
    or an orphaned provider invoice pointing at a purchase_id we never
    persisted — the latter needs manual admin attention).

    Returns:
        {"status": "ok", "purchase": dict, "telegram_id": int} on success
        {"status": "not_found"|"already_processed"|"invalid_status"} on failure
    """
    pending_purchase = await database.get_pending_purchase_any_status(purchase_id)

    if not pending_purchase:
        logger.error(
            f"{provider} webhook: purchase not found in DB: purchase_id={purchase_id} — "
            "row missing entirely, payment cannot be reconciled automatically"
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

    # Идемпотентность: mark-before-send через payment_notifications_sent.
    # finalize_purchase уже гарантирует single-writer через FOR UPDATE
    # SKIP LOCKED — но на нём защита СТАТУСА покупки, а не факта отправки
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

        # ── Bypass GB accumulation ─────────────────────────────────────
        # Единая точка добавления bypass GB (combo и обычная подписка).
        # sync_renewal_to_remnawave теперь ТОЛЬКО продлевает premium.expireAt
        # (см. purchase_flow.py) — bypass GB кладём здесь, ровно сколько
        # положено по тарифу, без дублей.
        #
        # Правила:
        #   combo_basic / combo_plus   → COMBO_TARIFFS[key][period]["gb"] GB
        #   basic / plus (обычные)     → TRAFFIC_LIMITS[tariff][period] bytes
        #   trial / telegram_* / biz   → skip (не имеют bypass ГБ по ТЗ)
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
            or subscription_type in config.BIZ_TARIFFS
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
                    asyncio.create_task(verify_premium_delivery(
                        telegram_id=telegram_id, provider=provider,
                        expected_expire_at=expires_at,
                        purchase_id=str(purchase_id), tariff=tariff_label,
                        period_days=_pd,
                    ))
                except Exception:
                    pass


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

    # Add traffic via Remnawave — clean primitive через numeric bypass id.
    # Если entity нет вообще (первый bypass-buy без подписки) — создаём.
    rmn_success = False
    pack = config.TRAFFIC_PACKS.get(traffic_gb) or config.TRAFFIC_PACKS_EXTENDED.get(traffic_gb)
    if pack:
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
        except TransientPaymentError:
            raise
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


