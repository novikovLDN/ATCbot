"""Приём оплаты через Telegram: сборка роутера и маршрутизация покупки.

ЧТО ЗДЕСЬ
    Только обработчик successful_payment, и в нём — один вопрос: ЧТО именно
    оплатили и кому это отдать. Всё предметное разложено по соседям:

        precheckout.py           подтверждение платежа до списания
        photo_log.py             запись file_id входящего фото (не про оплату)
        payment_preflight.py     проверки и сбор контекста до выдачи
        balance_topup.py         пополнение внутреннего баланса
        goods_delivery.py        выдача товаров мини-магазина
        purchase_routing.py      опознание типа покупки и суммы
        subscription_finalize.py выдача VPN-подписки
        subscription_success.py  экран «оплачено» и уборка после успеха
        combo_bypass.py          гигабайты обхода поверх подписки

    Разложено так, потому что в одном файле на 1148 строк лежал обработчик
    на тысячу строк: проверки, пополнение баланса, семь товаров, подписка,
    вёрстка и начисления делили полтора десятка локальных переменных, и
    вынести любую ветку без ручного разбора всей функции было нельзя.

ПОРЯДОК В ОБРАБОТЧИКЕ — ЭТО ПРАВИЛО, А НЕ СЛУЧАЙНОСТЬ
    Проверки → пополнение баланса → поиск покупки → выдача товара →
    ПРЕДОХРАНИТЕЛЬ → подписка. Предохранитель обязан стоять ДО финализации
    подписки: он ловит тип товара, который опознан, но не имеет обработчика.
    Без него человек, купивший Spotify, молча получил бы продление VPN, а
    заказ не дошёл бы до админа. Так уже терялись оплаты.

ЧТО ЛЕГКО СЛОМАТЬ
    Забытый include_router ниже. Обработчик остаётся объявленным, ошибок
    нет — событие оплаты просто перестаёт обрабатываться. Список подроутеров
    сторожит tests/services/test_payments_messages_split.py.

    Реэкспорт внизу: на classify_purchase / resolve_payment_amount_rubles /
    _ROUTED_PURCHASE_TYPES ссылаются через этот модуль тесты и соседний код.
    Убранное отсюда имя падает не при импорте, а в момент обращения.

VPN-ключ
    Инвариант архитектуры: бот НИКОГДА не собирает VLESS локально, ключ
    приходит только из API панели (см. subscription_finalize.py).
"""
import logging
import time

from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.services.payments import service as payment_service
from app.services.payments.exceptions import (
    PaymentServiceError,
    InvalidPaymentPayloadError,
)
from app.utils.logging_helpers import (
    log_handler_exit,
    classify_error,
)

payments_router = Router()
logger = logging.getLogger(__name__)


# Опознание типа покупки и суммы вынесено в отдельный модуль: им пользуется
# и выдача товаров (goods_delivery), иначе модули замкнулись бы кольцом.
# Имена реэкспортируются — на них ссылается существующий код и тесты.
from app.handlers.payments import goods_delivery as goods
from app.handlers.payments.goods_delivery import PaidPurchase
from app.handlers.payments.purchase_routing import (  # noqa: F401
    _ROUTED_PURCHASE_TYPES,
    _TARIFF_PREFIX_ROUTES,
    classify_purchase,
    resolve_payment_amount_rubles,
)
from app.handlers.payments.balance_topup import deliver_balance_topup
from app.handlers.payments.combo_bypass import grant_combo_and_bypass_traffic
from app.handlers.payments.payment_preflight import (  # noqa: F401
    PaymentEnvelope,
    PurchaseContext,
    load_purchase_context,
    prepare_successful_payment,
)
from app.handlers.payments.photo_log import photo_log_router
from app.handlers.payments.precheckout import precheckout_router
from app.handlers.payments.subscription_finalize import finalize_subscription
from app.handlers.payments.subscription_success import announce_success, finish_payment

# Подроутеры. Фильтры не пересекаются: pre_checkout_query — отдельный тип
# события, а фото и successful_payment взаимоисключающи внутри сообщения.
# Поэтому порядок подключения здесь ни на что не влияет — но добавляя роутер
# с более общим фильтром, проверьте это заново.
payments_router.include_router(precheckout_router)
payments_router.include_router(photo_log_router)


@payments_router.message(F.successful_payment)
async def process_successful_payment(message: Message, state: FSMContext):
    """Оплата картой или звёздами прошла — разобраться, что выдать.

    КРИТИЧНО:
    - Каждый тип товара обязан выйти через return в своей ветке
    - Всё, что до подписки не разобрано, ловит предохранитель ниже
    - FSM чистится только после успешной выдачи
    """
    start_time = time.time()

    # Проверки, kill-switch, готовность базы, язык и разбор платежа.
    # None означает, что человеку уже ответили и писать больше нечего.
    env = await prepare_successful_payment(message, start_time)
    if env is None:
        return

    telegram_id = env.telegram_id
    language = env.language
    payload = env.payload
    is_stars_payment = env.is_stars_payment

    # Проверяем, является ли это пополнением баланса
    try:
        payload_info = await payment_service.verify_payment_payload(payload, telegram_id)

        if payload_info.payload_type == "balance_topup":
            # Пополнение баланса зовём ВНУТРИ try осознанно: его исключения
            # обязаны попадать в те же две ветки ниже, что и раньше.
            await deliver_balance_topup(message, env, payload_info, start_time)
            return

    except InvalidPaymentPayloadError as e:
        logger.error(f"Invalid payment payload: {payload}, error={e}")
        language = await resolve_user_language(telegram_id)
        await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
        duration_ms = (time.time() - start_time) * 1000
        error_type = classify_error(e)
        log_handler_exit(
            handler_name="process_successful_payment",
            outcome="failed",
            telegram_id=telegram_id,
            operation="payment_finalization",
            error_type=error_type,
            duration_ms=duration_ms,
            reason="invalid_payload"
        )
        return
    except PaymentServiceError as e:
        logger.error(f"Payment service error: {e}")
        language = await resolve_user_language(telegram_id)
        await message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
        duration_ms = (time.time() - start_time) * 1000
        error_type = classify_error(e)
        log_handler_exit(
            handler_name="process_successful_payment",
            outcome="failed",
            telegram_id=telegram_id,
            operation="payment_finalization",
            error_type=error_type,
            duration_ms=duration_ms,
            reason="payment_service_error"
        )
        return

    # Ищем оплаченную покупку и её условия. None = покупка просрочена или
    # не найдена: деньги списаны, случай записан в аудит, человеку отвечено.
    ctx = await load_purchase_context(message, payload_info, env, start_time)
    if ctx is None:
        return

    purchase_id = ctx.purchase_id
    pending_purchase = ctx.pending_purchase
    payment_amount_rubles = ctx.payment_amount_rubles

    # Проверяем, является ли это подарочной подпиской
    # Выдача товаров мини-магазина.
    #
    # Раньше здесь подряд шли семь веток `if is_gift_purchase:` … и так далее,
    # на 290 строк, и все они делили локальные переменные обработчика. Теперь
    # каждая живёт своей функцией в goods_delivery, а сюда передаётся один
    # контекст. Функция сама проверяет, её ли это тип покупки, и возвращает
    # True, если оплату обработала.
    #
    # Порядок в списке значения не имеет: типы взаимоисключающие. Важно, что
    # список полный — забытый тип провалится ниже, в финализацию VPN-подписки,
    # и это ловит предохранитель после цикла.
    paid = PaidPurchase(
        message=message,
        state=state,
        telegram_id=telegram_id,
        language=language,
        purchase_id=purchase_id,
        pending_purchase=pending_purchase,
        payment_amount_rubles=payment_amount_rubles,
        is_stars_payment=is_stars_payment,
        start_time=start_time,
    )
    for deliver in (
        goods.deliver_gift,
        goods.deliver_premium,
        goods.deliver_stars,
        goods.deliver_steam,
        goods.deliver_spotify,
        goods.deliver_apple_id,
        goods.deliver_traffic_pack,
    ):
        if await deliver(paid):
            return


    # Предохранитель: всё, что ниже, финализируется как VPN-подписка.
    #
    # Каждый товар обязан иметь свою ветку выше и выйти через return. Если
    # classify_purchase опознал товарный тип, а ни одна ветка его не забрала —
    # значит тип добавили в _ROUTED_PURCHASE_TYPES и забыли обработчик. Молча
    # провалиться в подписку тут нельзя: человек заплатил за Spotify или
    # прокси, а получил бы продление VPN, и заказ не дошёл бы до админа.
    #
    # Поэтому: деньги фиксируем (покупка помечается оплаченной, чтобы не
    # потерять факт платежа), пользователю говорим, что заказ у поддержки,
    # админу шлём алерт. Ручной разбор дороже автоматики, но дешевле выдачи
    # чужого товара.
    _route = classify_purchase(pending_purchase)
    if _route != "subscription":
        logger.critical(
            "PURCHASE_ROUTE_UNHANDLED purchase_id=%s user=%s type=%s amount=%s "
            "— тип опознан, но обработчика нет; выдача НЕ выполнена, нужен ручной разбор",
            purchase_id, telegram_id, _route, payment_amount_rubles,
        )
        try:
            await database.mark_pending_purchase_paid(purchase_id)
        except Exception as e:
            logger.error("PURCHASE_ROUTE_UNHANDLED_MARK_FAILED purchase_id=%s: %s", purchase_id, e)
        try:
            from app.services.admin_alerts import alert_payment_failure
            await alert_payment_failure(
                message.bot, "telegram_payment", telegram_id, purchase_id,
                Exception(f"no handler for purchase_type={_route}"),
                is_transient=False, amount_rubles=payment_amount_rubles,
                tariff=pending_purchase.get("tariff"), period_days=None,
            )
        except Exception as e:
            logger.error("PURCHASE_ROUTE_UNHANDLED_ALERT_FAILED purchase_id=%s: %s", purchase_id, e)
        await message.answer(
            i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML",
        )
        await state.clear()
        return

    # Дальше — только VPN-подписка. Четыре шага строго по порядку, каждый
    # умеет сказать «дальше не идём»:
    #   1. финализация подписки (subscription_finalize.py)
    #   2. экран успеха; False = уведомление уже уходило раньше
    #   3. гигабайты обхода для комбо
    #   4. уборка: промо-сессия, FSM, аудит
    # Пропустить шаг 2 и перейти к 3 нельзя: повторное событие оплаты от
    # Telegram начислит трафик второй раз.
    fin = await finalize_subscription(message, state, env, ctx, start_time)
    if fin is None:
        return

    if not await announce_success(message, env, ctx, fin, start_time):
        return

    await grant_combo_and_bypass_traffic(state, env, ctx, fin)
    await finish_payment(state, env, ctx, fin, start_time)
