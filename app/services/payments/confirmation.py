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
from app.services.payments.exceptions import PaymentFinalizationError
from aiogram import Bot

from database.subscriptions import (
    PaymentAlreadyProcessed,
    PaymentAmountMismatch,
    PurchaseInvalidStatus,
    PurchaseLocked,
)

logger = logging.getLogger(__name__)

# Товары, которым здесь достаточно отметки «оплачено» и уведомления:
# подписку они не продлевают, выдаёт их человек или отдельный обработчик.
# Подмножество config.NON_SUBSCRIPTION_PURCHASE_TYPES — там же полный
# перечень и объяснение, почему список должен быть один.
MARK_PAID_ONLY_TYPES = (
    "telegram_stars", "telegram_premium", "steam", "proxy", "spotify",
)


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
    # Объявлено до try: ветка восстановления в except обращается к pending, и при
    # исключении на самом первом запросе это давало UnboundLocalError.
    pending: Optional[Dict[str, Any]] = None
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

        # Товары, которые не продлевают подписку: звёзды, Premium, Apple ID,
        # Steam, Spotify, прокси. Здесь их только помечаем оплаченными и
        # шлём уведомления — выдаёт их человек или отдельный обработчик.
        #
        # Раньше такой список существовал в трёх местах и в одном из них
        # расходился с остальными — там не было steam и proxy. Полный
        # перечень неподписочных типов теперь один,
        # config.NON_SUBSCRIPTION_PURCHASE_TYPES; согласованность с ним
        # проверяет tests/services/test_purchase_types.py.
        if (
            _purchase_type in MARK_PAID_ONLY_TYPES
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
                    from app.handlers.callbacks.apple_id import send_apple_id_success
                    await send_apple_id_success(bot, telegram_id, region, nominal, amount_rubles)
                elif _purchase_type == "spotify" or _tariff.startswith("spotify_"):
                    from app.handlers.payments.spotify_purchase import send_spotify_success
                    await send_spotify_success(bot, telegram_id, purchase_id, pending)
            except Exception as notif_err:
                # ЗДЕСЬ ТЕРЯЛСЯ ЗАКАЗ.
                #
                # Товар уже оплачен и помечен оплаченным. Уведомление
                # несёт две вещи: подтверждение покупателю и карточку
                # заказа админу с кнопкой выдачи. Если оно не ушло —
                # Telegram отдал 5xx, таймаут, что угодно, — покупатель
                # не увидел подтверждения, а админ не узнал о заказе.
                #
                # Повторный вебхук не спасал: mark_pending_purchase_paid
                # вернёт False, и ветка отправки будет пропущена как
                # дубль. Заказ оставался виден только прямым запросом в
                # pending_purchases, и никакого сигнала не приходило.
                #
                # Поэтому: громкий алерт админу отдельным каналом. Он не
                # заменяет карточку заказа, но даёт человеку знать, что
                # заказ есть и его надо достать руками.
                logger.error(
                    f"{provider} webhook: notification failed for {_purchase_type}: {notif_err}",
                    exc_info=True,
                )
                try:
                    from app.services.admin_alerts import send_alert
                    await send_alert(
                        bot, "payment",
                        (
                            f"⚠️ Заказ оплачен, но уведомление не ушло\n\n"
                            f"Тип: {_purchase_type}\n"
                            f"Тариф: {_tariff or '—'}\n"
                            f"Покупатель: {telegram_id}\n"
                            f"purchase_id: {purchase_id}\n"
                            f"Сумма: {amount_rubles:.2f} ₽\n"
                            f"Провайдер: {provider}\n"
                            f"Ошибка: {type(notif_err).__name__}: {str(notif_err)[:200]}\n\n"
                            f"Покупатель не получил подтверждения, карточка заказа "
                            f"не пришла. Заказ нужно выдать вручную."
                        ),
                        force=True,
                    )
                except Exception as alert_err:
                    logger.critical(
                        "%s webhook: ORDER_LOST purchase_id=%s type=%s user=%s — "
                        "не удалось ни уведомить, ни поднять алерт: %s",
                        provider, purchase_id, _purchase_type, telegram_id, alert_err,
                    )
                return {
                    "status": "ok",
                    "purchase_id": purchase_id,
                    "notification_failed": True,
                }

            return {"status": "ok", "purchase_id": purchase_id}

        # Через сервисный слой, а не напрямую в database.
        #
        # Обёртка сама проверяет success и приводит любую ошибку к
        # PaymentFinalizationError. Раньше отсюда звали database напрямую и
        # разбирали результат вручную, бросая голый Exception — при одной и
        # той же ошибке вебхук отвечал провайдеру не тем, чем экран в боте,
        # а шаг, добавленный в обёртку (метрика, идемпотентный лог),
        # обходился стороной.
        from app.services.subscriptions import service as subscription_service

        result = await subscription_service.finalize_purchase(
            purchase_id=purchase_id,
            payment_provider=provider,
            amount_rubles=amount_rubles,
            invoice_id=str(invoice_id),
        )

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
        is_gift = bool(result.get("is_gift") and result.get("gift_code"))
        is_farm_effect = bool(result.get("is_farm_effect"))

        # Notification failure must NOT fail the payment — DB is already committed
        try:
            if is_gift:
                # Подарок: finalize_purchase уже создал gift_code, но покупателю
                # нужна ссылка на подарок, а не «ваша подписка активна».
                #
                # Раньше gift сюда попадал и уходил в общий _send_confirmation:
                # expires_at у подарка нет, поэтому человек получал текст
                # «Оплата получена! До: N/A» с кнопками подключения VPN, а код
                # подарка оставался только в базе. Оплата картой в Telegram
                # (payments_messages.py) обрабатывала подарок правильно —
                # расходились только вебхуки CryptoBot/Lava/Платеги.
                await _send_gift_confirmation(
                    provider=provider,
                    bot=bot,
                    telegram_id=telegram_id,
                    purchase_id=purchase_id,
                    result=result,
                )
            elif is_farm_effect:
                # Плёнка от шторма. Отдельная ветка нужна по той же причине,
                # что и у подарка: у покупки нет expires_at, и общий текст
                # подтверждения врал бы «Тариф: Basic, До: N/A».
                await _send_farm_shield_confirmation(
                    provider=provider,
                    bot=bot,
                    telegram_id=telegram_id,
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

            # Кешбэк рефереру начислен внутри finalize_purchase, но сообщение
            # отправляет вызывающий код. Вебхуки этот словарь не читали вовсе:
            # деньги реферер получал, уведомление — никогда. Остальные пути
            # оплаты (карта в Telegram, покупка с баланса) уведомление слали.
            from app.handlers.notifications import notify_referral_cashback
            await notify_referral_cashback(
                bot,
                result.get("referral_reward"),
                referred_id=telegram_id,
                purchase_amount=amount_rubles,
                action_type="topup" if is_balance_topup else "purchase",
                period_days=result.get("period_days"),
                context=f"webhook:{provider}",
            )
        except TransientPaymentError as combo_err:
            # НЕ уведомление, а сорванная выдача товара.
            #
            # _send_confirmation вызывается внутри этого try и бросает
            # TransientPaymentError, когда комбо-тарифу не удалось начислить
            # bypass-трафик: расчёт был на то, что вебхук ответит 5xx и
            # провайдер повторит платёж. Но except ниже ловит любое
            # Exception, и сигнал гасился записью «payment was successful» —
            # человек оплачивал комбо, гигабайты не приходили, повтора не
            # было, и в логах это выглядело как неудавшееся уведомление.
            #
            # Поведение здесь намеренно не меняется (это отдельный дефект,
            # см. отчёт) — но запись обязана называть вещи своими именами:
            # товар не выдан, и разбирать это придётся руками.
            logger.critical(
                "PAYMENT_DELIVERY_FAILED_SILENTLY: provider=%s, user=%s, "
                "purchase_id=%s, payment_id=%s, error=%s — оплата принята, товар "
                "НЕ выдан; сигнал на повтор вебхука проглочен, нужен ручной разбор",
                provider, telegram_id, purchase_id, payment_id, combo_err,
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
            # Подарок исключён намеренно: подписка покупателя не менялась,
            # синхронизировать на сайт нечего — иначе ему туда уехал бы
            # выдуманный «купленный тариф на 30 дней».
            if (
                site_sync_enabled()
                and not is_balance_topup
                and not is_traffic_pack
                and not is_gift
                and not is_farm_effect
            ):
                period_days = result.get("period_days", 30)
                tariff_type = result.get("tariff_type", "basic")
                asyncio.ensure_future(full_sync_after_payment(
                    telegram_id, period_days, tariff_type, amount_rubles, purchase_id,
                ))
        except Exception as sync_err:
            logger.warning("SITE_SYNC_FIRE_AND_FORGET_ERROR: %s", sync_err)

    except (PurchaseLocked, PurchaseInvalidStatus, PaymentAmountMismatch) as e:
        # Раньше все эти причины попадали в общий except ValueError вместе с
        # «уже обработано»: провайдеру уходил HTTP 200 already_processed, повтора
        # не было, алерт не поднимался. Расхождение суммы означало, что деньги
        # списаны, товар не выдан и об этом никто не узнал.
        reason = type(e).__name__
        logger.error(
            f"PAYMENT_REJECTED: provider={provider}, user={telegram_id}, "
            f"purchase_id={purchase_id}, reason={reason}, error={e}"
        )
        from app.services.admin_alerts import alert_payment_failure
        tariff, period_days = await _lookup_purchase_tariff(purchase_id)
        await alert_payment_failure(
            bot, provider, telegram_id, purchase_id, e, is_transient=False,
            amount_rubles=amount_rubles, tariff=tariff, period_days=period_days,
        )
        return {"status": "error", "reason": reason}
    except PaymentAlreadyProcessed as e:
        logger.info(
            f"{provider} webhook: purchase already processed: "
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
    except PaymentFinalizationError as e:
        # Сервисная обёртка приводит ЛЮБУЮ ошибку выдачи к своему типу, в
        # том числе временную: RuntimeError из provision_subscription, когда
        # панель ответила не 2xx.
        #
        # Разбирать её надо по первопричине. Иначе моргнувшая панель
        # выглядела бы как окончательный отказ, вебхук отвечал бы 200, и
        # провайдер не повторил бы платёж — человек заплатил и остался без
        # подписки.
        cause = e.__cause__
        if isinstance(cause, (asyncpg.PostgresError, asyncio.TimeoutError, OSError, RuntimeError)):
            logger.error(
                f"PAYMENT_TRANSIENT_ERROR: provider={provider}, user={telegram_id}, "
                f"purchase_id={purchase_id}, error={type(cause).__name__}: {cause}"
            )
            from app.services.admin_alerts import alert_payment_failure
            tariff, period_days = await _lookup_purchase_tariff(purchase_id)
            await alert_payment_failure(
                bot, provider, telegram_id, purchase_id, cause, is_transient=True,
                amount_rubles=amount_rubles, tariff=tariff, period_days=period_days,
            )
            raise TransientPaymentError(
                f"Transient error during payment: {type(cause).__name__}: {cause}"
            ) from e

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
    except (json.JSONDecodeError, TypeError) as e:
        # Точка входа денежного пути: без purchase_id платёж не привязать ни к
        # какой покупке — деньги придут, товар не выдастся. Возврат None молча
        # оставлял разбор без причины: непонятно, payload пришёл битый, пустой
        # или другой структуры. Само тело не пишем — оно приходит от внешнего
        # провайдера, может содержать что угодно и годится для подмены строк
        # в логе; хватает типа ошибки и длины.
        logger.error(
            "PAYLOAD_PARSE_FAILED: не удалось извлечь purchase_id, "
            "error=%s: %s, payload_type=%s, payload_len=%s",
            type(e).__name__, e, type(payload_raw).__name__,
            len(payload_raw) if isinstance(payload_raw, (str, bytes)) else "n/a",
        )
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
        # Строки нет — алерт о сбое оплаты уйдёт без тарифа и срока. Знать об
        # этом надо: пустые поля в алерте выглядят как «товар не определён»,
        # хотя на деле не нашлась запись покупки.
        logger.warning(
            "PURCHASE_TARIFF_LOOKUP_EMPTY purchase_id=%s — алерт уйдёт без тарифа",
            purchase_id,
        )
    except Exception as e:
        # Вызывается только с путей, где оплата уже сорвалась, и молчание
        # здесь съедало вторую ошибку поверх первой: в алерте админу тариф и
        # срок оказывались пустыми без единого следа почему.
        logger.warning(
            "PURCHASE_TARIFF_LOOKUP_FAILED purchase_id=%s error=%s: %s",
            purchase_id, type(e).__name__, e,
        )
    return None, None


async def _send_farm_shield_confirmation(
    provider: str,
    bot: Bot,
    telegram_id: int,
    purchase_id: str,
    result: dict,
) -> None:
    """Сообщить об исходе покупки плёнки от шторма.

    Два исхода, и оба нужно проговорить вслух:

    • плёнка применилась — просто подтверждаем;
    • не применилась (грядку успели собрать, она уже накрыта, статус больше
      не growing, шторм уже прошёл) — finalize_purchase вернул деньги на
      баланс, и человек обязан это увидеть. Иначе картина у него такая:
      деньги списаны, плёнки нет, бот отвечает «оплата получена» — и он идёт
      в поддержку. Именно так и было: ветки для farm_effect здесь не
      существовало вовсе, и покупатель получал общий текст подписки
      «Тариф: Basic, До: N/A».
    """
    plot_id = result.get("farm_plot_id")
    plot_label = f"Грядка {int(plot_id) + 1}" if plot_id is not None else "Грядка"
    refund_kopecks = int(result.get("farm_shield_refund_kopecks") or 0)

    if result.get("farm_shield_applied"):
        text = (
            f"🛡 <b>Плёнка установлена</b>\n\n"
            f"{plot_label} защищена от ближайшего шторма."
        )
    elif refund_kopecks > 0:
        text = (
            "⚠️ <b>Плёнка не пригодилась</b>\n\n"
            f"{plot_label} к моменту оплаты уже была собрана или защищена, "
            "поэтому устанавливать плёнку было не на что.\n\n"
            f"💰 <b>{refund_kopecks / 100:.2f} ₽</b> вернулись на ваш баланс — "
            "их можно потратить на подписку, трафик или новую плёнку."
        )
    else:
        text = (
            "⚠️ <b>Плёнка не установлена</b>\n\n"
            f"{plot_label} к моменту оплаты уже была собрана или защищена. "
            "Напишите в поддержку — разберёмся с платежом."
        )

    try:
        await bot.send_message(telegram_id, text, parse_mode="HTML")
    except Exception as send_err:
        logger.warning(
            "%s: failed to send farm shield confirmation to user=%s: %s",
            provider, telegram_id, send_err,
        )
    logger.info(
        "FARM_SHIELD_CONFIRMATION provider=%s purchase_id=%s user=%s applied=%s refund_kopecks=%s",
        provider, purchase_id, telegram_id,
        result.get("farm_shield_applied"), refund_kopecks,
    )


async def _send_gift_confirmation(
    provider: str,
    bot: Bot,
    telegram_id: int,
    purchase_id: str,
    result: dict,
) -> None:
    """Отправить покупателю ссылку на оплаченный подарок.

    Зачем отдельная функция: подарок — единственный тип покупки, где деньги
    платит один человек, а подписку получает другой. Обычное подтверждение
    («ваша подписка активна до …») здесь бессмысленно: у подарка нет
    expires_at, и человеку нужна не кнопка подключения, а ссылка, которую он
    перешлёт получателю.

    Текст и клавиатуру строит _send_gift_success из gift.py — та же функция,
    что и при оплате картой в Telegram. Так вебхуки и Telegram Payments не
    расходятся в том, что видит покупатель.
    """
    from app.services.language_service import resolve_user_language
    from app.handlers.callbacks.gift import _send_gift_success

    language = await resolve_user_language(telegram_id)
    await _send_gift_success(
        bot=bot,
        telegram_id=telegram_id,
        language=language,
        gift_code=result["gift_code"],
        tariff=result.get("gift_tariff") or "basic",
        period_days=int(result.get("gift_period_days") or 30),
    )
    # Код подарка — предъявительский токен на оплаченную подписку: кто его
    # прочитал, тот её и активирует (start.py принимает /start gift_<код>).
    # В логи идёт маска, а цепочка собирается по purchase_id.
    from app.utils.security import mask_secret
    logger.info(
        "GIFT_PAYMENT_FINALIZED provider=%s purchase_id=%s user=%s code=%s",
        provider, purchase_id, telegram_id, mask_secret(result["gift_code"]),
    )


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

        # Комбо: гигабайты обхода. Объём считает app/services/combo_traffic —
        # единственное место, где он берётся из COMBO_TARIFFS.
        #
        # Отложенная активация сюда не идёт: подписка ещё не выдана, панель
        # обычно и есть причина отсрочки, а начисление сделает
        # activation_worker при активации. Начислять в обоих местах нельзя —
        # человек получит пакет дважды.
        if is_combo and result.get("activation_status") == "pending":
            logger.info(
                "COMBO_TRAFFIC_DEFERRED: provider=%s user=%s purchase_id=%s "
                "— активация отложена, ГБ начислит activation_worker",
                provider, telegram_id, purchase_id,
            )
        elif is_combo:
            from app.services.combo_traffic import grant_combo_traffic
            outcome = await grant_combo_traffic(
                telegram_id,
                subscription_type,
                result.get("period_days", 30) or 30,
                is_combo=True,
                purchase_id=purchase_id,
                subscription_end=expires_at,
                source=f"webhook:{provider}",
            )
            if not outcome.granted:
                # На вебхуке отказ обязан подниматься: провайдер повторит
                # запрос. Интерактивные пути так делать не могут — там деньги
                # уже взяты и экран успеха уже показан.
                raise TransientPaymentError(
                    f"combo bypass-traffic not granted ({outcome.reason}): "
                    f"user={telegram_id} gb={outcome.gb}"
                )


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


