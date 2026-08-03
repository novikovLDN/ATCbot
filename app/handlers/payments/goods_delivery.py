"""Выдача товаров мини-магазина после оплаты через Telegram Payments.

ЧТО ЗДЕСЬ
    По одной функции на каждый тип товара: подарочная подписка, Telegram
    Premium, пакет Stars, Steam, Spotify, Apple ID и пакет трафика. Каждая
    получает единый контекст оплаты и возвращает True, если оплату
    обработала.

ПОЧЕМУ ОТДЕЛЬНЫЙ МОДУЛЬ
    Раньше все семь веток жили внутри одного обработчика
    process_successful_payment на 1305 строк. Ветки шли подряд, делили
    полтора десятка локальных переменных, и понять, где заканчивается одна
    и начинается другая, можно было только по комментариям. Правка выдачи
    Spotify означала правку в середине файла, где сверху подписка, а снизу
    трафик.

ГЛАВНОЕ ПРАВИЛО
    Порядок внутри каждой функции: СНАЧАЛА пометить покупку оплаченной,
    ПОТОМ отправлять уведомления. Повторный вебхук от Telegram — штатная
    ситуация; если пометка идёт второй, человек и админ получат дубли.
    mark_pending_purchase_paid возвращает False, если покупка уже помечена,
    — это и есть защита от повторной выдачи.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Any, Optional

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from aiogram.fsm.context import FSMContext

import config
import database
from app.i18n import get_text as i18n_get_text
from app.utils.logging_helpers import log_handler_exit
from app.handlers.payments.purchase_routing import classify_purchase
# Финализация покупки идёт только через сервисный слой.
#
# Прямой вызов database.finalize_purchase в обход сервиса означал, что часть
# оплат (подарок и пакет трафика) не проходила через единую точку, где
# проверяется результат, пишется лог и различаются доменные ошибки
# («уже обработано» / «сумма не сошлась» / «покупка заперта»). Разные пути
# — разное поведение при повторной оплате, а повторный вебхук у Telegram
# штатная ситуация.
from app.services.subscriptions import service as subscription_service

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PaidPurchase:
    """Всё, что нужно знать о состоявшейся оплате, чтобы выдать товар.

    Собирается один раз в process_successful_payment и передаётся в
    функцию выдачи. Раньше эти девять значений были локальными переменными
    обработчика, и каждая ветка молча зависела от того, что лежит выше по
    коду, — вынести ветку было нельзя, не разобрав вручную всю функцию.
    """

    message: Message
    state: FSMContext
    telegram_id: int
    language: str
    purchase_id: str
    pending_purchase: dict
    payment_amount_rubles: float
    is_stars_payment: bool
    start_time: float


async def deliver_gift(paid: PaidPurchase) -> bool:
    """Подарочная подписка: создаём код и присылаем ссылку покупателю.

    Возвращает True, если оплата этого типа обработана.
    """
    message = paid.message
    state = paid.state
    telegram_id = paid.telegram_id
    language = paid.language
    purchase_id = paid.purchase_id
    pending_purchase = paid.pending_purchase
    payment_amount_rubles = paid.payment_amount_rubles
    is_stars_payment = paid.is_stars_payment
    start_time = paid.start_time

    if classify_purchase(pending_purchase) != "gift":
        return False

    payment_provider_name = "telegram_stars" if is_stars_payment else "telegram_payment"
    try:
        gift_result = await subscription_service.finalize_purchase(
            purchase_id=purchase_id,
            payment_provider=payment_provider_name,
            amount_rubles=payment_amount_rubles,
        )
        if gift_result and gift_result.get("is_gift") and gift_result.get("gift_code"):
            from app.handlers.callbacks.gift import _send_gift_success
            await _send_gift_success(
                bot=message.bot,
                telegram_id=telegram_id,
                language=language,
                gift_code=gift_result["gift_code"],
                tariff=gift_result["gift_tariff"],
                period_days=gift_result["gift_period_days"],
            )
            logger.info(
                f"GIFT_PAYMENT_FINALIZED purchase_id={purchase_id} user={telegram_id} "
                f"code={gift_result['gift_code']}"
            )
            await state.clear()
            duration_ms = (time.time() - start_time) * 1000
            log_handler_exit(
                handler_name="process_successful_payment",
                outcome="success",
                telegram_id=telegram_id,
                operation="payment_finalization",
                duration_ms=duration_ms,
                payment_type="gift_subscription",
            )
            return True
        else:
            logger.error(f"Gift finalization returned unexpected result: {gift_result}")
            await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
            return True
    except Exception as e:
        logger.exception(f"Gift payment finalization failed: user={telegram_id}, error={e}")
        await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
        return True


async def deliver_premium(paid: PaidPurchase) -> bool:
    """Telegram Premium: помечаем оплаченным и уведомляем админа.

    Возвращает True, если оплата этого типа обработана.
    """
    message = paid.message
    state = paid.state
    telegram_id = paid.telegram_id
    language = paid.language
    purchase_id = paid.purchase_id
    pending_purchase = paid.pending_purchase
    payment_amount_rubles = paid.payment_amount_rubles
    is_stars_payment = paid.is_stars_payment
    start_time = paid.start_time

    if classify_purchase(pending_purchase) != "telegram_premium":
        return False

    try:
        # Комментарий «send BEFORE marking paid» описывал ложную заботу:
        # данные покупки уже прочитаны в pending_purchase и доступны после
        # пометки. Зато при сбое пометки человек и админ получали сообщения,
        # а покупка оставалась pending — следующий вебхук слал их повторно.
        if not await database.mark_pending_purchase_paid(purchase_id):
            logger.info(
                "PREMIUM_ALREADY_FINALIZED purchase_id=%s — уведомления пропущены",
                purchase_id,
            )
            await state.clear()
            return True
        from app.handlers.payments.telegram_premium import send_premium_success
        await send_premium_success(
            message.bot, telegram_id, purchase_id, pending_purchase,
        )
        logger.info(
            "PREMIUM_PAYMENT_FINALIZED purchase_id=%s user=%s amount=%s",
            purchase_id, telegram_id, payment_amount_rubles,
        )
    except Exception as e:
        logger.exception("PREMIUM_PAYMENT_ERROR purchase_id=%s error=%s", purchase_id, e)
        await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
    await state.clear()
    duration_ms = (time.time() - start_time) * 1000
    log_handler_exit(
        handler_name="process_successful_payment",
        outcome="success",
        telegram_id=telegram_id,
        operation="payment_finalization",
        duration_ms=duration_ms,
        payment_type="telegram_premium",
    )
    return True


async def deliver_stars(paid: PaidPurchase) -> bool:
    """Пакет Telegram Stars: помечаем оплаченным и уведомляем админа.

    Возвращает True, если оплата этого типа обработана.
    """
    message = paid.message
    state = paid.state
    telegram_id = paid.telegram_id
    language = paid.language
    purchase_id = paid.purchase_id
    pending_purchase = paid.pending_purchase
    payment_amount_rubles = paid.payment_amount_rubles
    is_stars_payment = paid.is_stars_payment
    start_time = paid.start_time

    if classify_purchase(pending_purchase) != "telegram_stars":
        return False

    try:
        # Сначала помечаем покупку оплаченной, только потом уведомляем.
        # mark_pending_purchase_paid возвращает False, если пометка уже
        # стоит — это и есть защита от повторных уведомлений. При обратном
        # порядке сбой пометки оставлял покупку в pending, хотя человек и
        # админ уже получили сообщения, и следующий вебхук слал их заново.
        if not await database.mark_pending_purchase_paid(purchase_id):
            logger.info(
                "STARS_ALREADY_FINALIZED purchase_id=%s — уведомления пропущены",
                purchase_id,
            )
            await state.clear()
            return True
        from app.handlers.payments.telegram_stars_purchase import send_stars_success
        await send_stars_success(
            message.bot, telegram_id, purchase_id, pending_purchase,
        )
        logger.info(
            "STARS_PAYMENT_FINALIZED purchase_id=%s user=%s amount=%s",
            purchase_id, telegram_id, payment_amount_rubles,
        )
    except Exception as e:
        logger.exception("STARS_PAYMENT_ERROR purchase_id=%s error=%s", purchase_id, e)
        await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
    await state.clear()
    duration_ms = (time.time() - start_time) * 1000
    log_handler_exit(
        handler_name="process_successful_payment",
        outcome="success",
        telegram_id=telegram_id,
        operation="payment_finalization",
        duration_ms=duration_ms,
        payment_type="telegram_stars",
    )
    return True


async def deliver_steam(paid: PaidPurchase) -> bool:
    """Пополнение Steam: помечаем оплаченным и уведомляем админа.

    Возвращает True, если оплата этого типа обработана.
    """
    message = paid.message
    state = paid.state
    telegram_id = paid.telegram_id
    language = paid.language
    purchase_id = paid.purchase_id
    pending_purchase = paid.pending_purchase
    payment_amount_rubles = paid.payment_amount_rubles
    is_stars_payment = paid.is_stars_payment
    start_time = paid.start_time

    if classify_purchase(pending_purchase) != "steam":
        return False

    try:
        # Порядок как в ветке Stars: пометка первой, она же защита от дублей.
        if not await database.mark_pending_purchase_paid(purchase_id):
            logger.info(
                "STEAM_ALREADY_FINALIZED purchase_id=%s — уведомления пропущены",
                purchase_id,
            )
            await state.clear()
            return True
        from app.handlers.payments.steam_purchase import send_steam_success
        await send_steam_success(
            message.bot, telegram_id, purchase_id, pending_purchase,
        )
        logger.info(
            "STEAM_PAYMENT_FINALIZED purchase_id=%s user=%s amount=%s",
            purchase_id, telegram_id, payment_amount_rubles,
        )
    except Exception as e:
        logger.exception("STEAM_PAYMENT_ERROR purchase_id=%s error=%s", purchase_id, e)
        await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
    await state.clear()
    duration_ms = (time.time() - start_time) * 1000
    log_handler_exit(
        handler_name="process_successful_payment",
        outcome="success",
        telegram_id=telegram_id,
        operation="payment_finalization",
        duration_ms=duration_ms,
        payment_type="steam",
    )
    return True


async def deliver_spotify(paid: PaidPurchase) -> bool:
    """Spotify Premium: помечаем оплаченным, заказ уходит админу с данными аккаунта.

    Возвращает True, если оплата этого типа обработана.
    """
    message = paid.message
    state = paid.state
    telegram_id = paid.telegram_id
    language = paid.language
    purchase_id = paid.purchase_id
    pending_purchase = paid.pending_purchase
    payment_amount_rubles = paid.payment_amount_rubles
    is_stars_payment = paid.is_stars_payment
    start_time = paid.start_time

    if classify_purchase(pending_purchase) != "spotify":
        return False

    try:
        # Порядок как в остальных товарных ветках: пометка первой.
        if not await database.mark_pending_purchase_paid(purchase_id):
            logger.info(
                "SPOTIFY_ALREADY_FINALIZED purchase_id=%s — уведомления пропущены",
                purchase_id,
            )
            await state.clear()
            return True
        from app.handlers.payments.spotify_purchase import send_spotify_success
        await send_spotify_success(
            message.bot, telegram_id, purchase_id, pending_purchase,
        )
        logger.info(
            "SPOTIFY_PAYMENT_FINALIZED purchase_id=%s user=%s amount=%s",
            purchase_id, telegram_id, payment_amount_rubles,
        )
    except Exception as e:
        logger.exception("SPOTIFY_PAYMENT_ERROR purchase_id=%s error=%s", purchase_id, e)
        await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
    await state.clear()
    duration_ms = (time.time() - start_time) * 1000
    log_handler_exit(
        handler_name="process_successful_payment",
        outcome="success",
        telegram_id=telegram_id,
        operation="payment_finalization",
        duration_ms=duration_ms,
        payment_type="spotify",
    )
    return True


async def deliver_apple_id(paid: PaidPurchase) -> bool:
    """Пополнение Apple ID: помечаем оплаченным и уведомляем админа.

    Возвращает True, если оплата этого типа обработана.
    """
    message = paid.message
    state = paid.state
    telegram_id = paid.telegram_id
    language = paid.language
    purchase_id = paid.purchase_id
    pending_purchase = paid.pending_purchase
    payment_amount_rubles = paid.payment_amount_rubles
    is_stars_payment = paid.is_stars_payment
    start_time = paid.start_time

    if classify_purchase(pending_purchase) != "apple_id":
        return False

    try:
        tariff = pending_purchase.get("tariff", "apple_id_usa_0")
        tariff_parts = tariff.split("_")
        region = tariff_parts[2] if len(tariff_parts) >= 3 else "usa"
        nominal = int(tariff_parts[3]) if len(tariff_parts) >= 4 else 0

        # Порядок как в остальных товарных ветках: пометка первой.
        if not await database.mark_pending_purchase_paid(purchase_id):
            logger.info(
                "APPLE_ALREADY_FINALIZED purchase_id=%s — уведомления пропущены",
                purchase_id,
            )
            await state.clear()
            return True
        from app.handlers.callbacks.apple_id import send_apple_id_success
        await send_apple_id_success(
            message.bot, telegram_id, region, nominal, payment_amount_rubles,
        )
        logger.info("APPLE_PAYMENT_FINALIZED purchase_id=%s user=%s", purchase_id, telegram_id)
    except Exception as e:
        logger.exception("APPLE_PAYMENT_ERROR purchase_id=%s error=%s", purchase_id, e)
        await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
    await state.clear()
    return True


async def deliver_traffic_pack(paid: PaidPurchase) -> bool:
    """Пакет трафика: финализируем покупку и начисляем ГБ в Remnawave.

    Возвращает True, если оплата этого типа обработана.
    """
    message = paid.message
    state = paid.state
    telegram_id = paid.telegram_id
    language = paid.language
    purchase_id = paid.purchase_id
    pending_purchase = paid.pending_purchase
    payment_amount_rubles = paid.payment_amount_rubles
    is_stars_payment = paid.is_stars_payment
    start_time = paid.start_time

    if classify_purchase(pending_purchase) != "traffic_pack":
        return False

    payment_provider_name = "telegram_stars" if is_stars_payment else "telegram_payment"
    try:
        traffic_result = await subscription_service.finalize_purchase(
            purchase_id=purchase_id,
            payment_provider=payment_provider_name,
            amount_rubles=payment_amount_rubles,
        )
        if traffic_result and traffic_result.get("is_traffic_pack"):
            traffic_gb = traffic_result.get("traffic_gb", 0)
            _tariff_tag = pending_purchase.get("tariff", "")
            _is_bypass = _tariff_tag.startswith("bypass_")

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
                            "TRAFFIC_PACK_REMNAWAVE_ERROR: user=%s gb=%s error=%s",
                            telegram_id, traffic_gb, rmn_err,
                        )
                if not rmn_success:
                    # No UUID or stale (404) — clear and create fresh
                    if rmn_uuid:
                        await database.clear_remnawave_uuid(telegram_id)
                    try:
                        from app.services import remnawave_service
                        # Локальный импорт datetime здесь делал имена datetime и
                        # timezone локальными на всю функцию, из-за чего строка 210
                        # падала с UnboundLocalError, а except Exception её глушил —
                        # блок оценки состояния системы не работал никогда.
                        far_future = datetime.now(timezone.utc) + timedelta(days=3650)
                        await remnawave_service.create_remnawave_user(
                            telegram_id, "basic", far_future,
                            traffic_limit_override=traffic_bytes,
                        )
                        rmn_success = True
                        logger.info("BYPASS_REMNAWAVE_USER_CREATED user=%s gb=%s", telegram_id, traffic_gb)
                    except Exception as rmn_err:
                        logger.error(
                            "TRAFFIC_PACK_REMNAWAVE_CREATE_ERROR: user=%s gb=%s error=%s",
                            telegram_id, traffic_gb, rmn_err,
                        )
            else:
                logger.error(
                    "TRAFFIC_PACK_INVALID_GB: user=%s gb=%s purchase=%s",
                    telegram_id, traffic_gb, purchase_id,
                )

            # Bypass-only: activate 3-day trial if eligible
            _trial_activated = False
            if _is_bypass:
                try:
                    from app.services.trials import service as trial_service
                    if await trial_service.is_trial_available(telegram_id):
                        await trial_service.activate_trial(telegram_id)
                        _trial_activated = True
                        logger.info("BYPASS_TRIAL_ACTIVATED user=%s", telegram_id)
                except Exception as trial_err:
                    logger.warning("BYPASS_TRIAL_FAIL user=%s: %s", telegram_id, trial_err)

            if _is_bypass:
                text = i18n_get_text(language, "bypass.purchase_success", gb=traffic_gb)
                if _trial_activated:
                    text += "\n\n" + i18n_get_text(language, "bypass.trial_activated")
            else:
                text = i18n_get_text(language, "traffic.purchase_success", gb=traffic_gb, price="")
            if not rmn_success:
                text += "\n\n⚠️ Активация трафика задерживается. Обратитесь в поддержку, если не применится в течение часа."
                logger.error(
                    "TRAFFIC_PACK_NOT_APPLIED: user=%s gb=%s purchase=%s — needs manual resolution",
                    telegram_id, traffic_gb, purchase_id,
                )
            if _is_bypass:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👤 Личный кабинет", callback_data="menu_profile")],
                    [InlineKeyboardButton(
                        text="Купить ещё ГБ",
                        callback_data="buy_traffic",
                        icon_custom_emoji_id="5199785165735367039",  # ⚡️
                    )],
                    [InlineKeyboardButton(text="← На главную", callback_data="menu_main")],
                ])
            else:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text=i18n_get_text(language, "common.back"),
                        callback_data="menu_main",
                    )],
                ])
            await message.answer(text, reply_markup=kb, parse_mode="HTML")

            logger.info(
                "TRAFFIC_PACK_PAYMENT_FINALIZED purchase_id=%s user=%s gb=%s",
                purchase_id, telegram_id, traffic_gb,
            )
        else:
            logger.error(f"Traffic pack finalization unexpected result: {traffic_result}")
            await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
    except Exception as e:
        logger.exception(f"Traffic pack payment finalization failed: user={telegram_id}, error={e}")
        await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
    await state.clear()
    duration_ms = (time.time() - start_time) * 1000
    log_handler_exit(
        handler_name="process_successful_payment",
        outcome="success",
        telegram_id=telegram_id,
        operation="payment_finalization",
        duration_ms=duration_ms,
        payment_type="traffic_pack",
    )
    return True
