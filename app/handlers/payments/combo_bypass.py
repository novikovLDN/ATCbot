"""Комбо и обход: гигабайты Remnawave поверх оплаченной подписки.

ЧТО ЗДЕСЬ
    Одна функция — начисление трафика обхода после успешной оплаты и
    продление пользователя Remnawave для обычных подписок.

ПОЧЕМУ ВЫДЕЛЕНО
    Это единственная часть обработчика, которая ходит во внешнюю панель.
    Смешивать её с вёрсткой экрана успеха незачем: ломается и правится она
    по своим поводам (доступность Remnawave, состав COMBO_TARIFFS).

ЧТО ЛЕГКО СЛОМАТЬ
    Признак комбо. FSM (combo_bypass_gb) — быстрый путь, но не источник
    правды: между счётом и оплатой человек успевает открыть другое меню
    (FSM очищается) или бот перезапускается (FSM в памяти теряется).
    Поэтому признак берётся ещё и из result.is_combo — он приходит из
    pending_purchases.is_combo, то есть из базы. Оставите только FSM —
    покупатели комбо получат подписку и НОЛЬ гигабайтов обхода, без единой
    ошибки в логах.

    Объём ГБ здесь не считается вовсе: его знает app/services/combo_traffic.
    Появится второй расчёт — копии разъедутся, как это уже было.

    Взаимоисключение веток: обычное продление Remnawave делается только для
    некомбо-покупок, иначе трафик начислит grant_combo_traffic. Позовёте
    оба — продление затрёт начисление.

    Ни одно исключение отсюда не должно всплывать наверх: деньги уже взяты,
    подписка уже выдана. Поэтому каждый блок в своём try.
"""
import logging

import config
import database
from aiogram.fsm.context import FSMContext

from app.handlers.payments.payment_preflight import PaymentEnvelope, PurchaseContext
from app.handlers.payments.subscription_finalize import FinalizedSubscription
from app.services.combo_traffic import grant_combo_traffic

logger = logging.getLogger(__name__)


async def grant_combo_and_bypass_traffic(
    state: FSMContext,
    env: PaymentEnvelope,
    ctx: PurchaseContext,
    fin: FinalizedSubscription,
) -> None:
    """Начислить гигабайты обхода и/или продлить пользователя в Remnawave."""
    telegram_id = env.telegram_id
    tariff_type = ctx.tariff_type
    period_days = ctx.period_days
    purchase_id = ctx.purchase_id
    result = fin.result
    expires_at = fin.expires_at

    fsm_data = await state.get_data()
    # Признак комбо: сначала база (result.is_combo пришёл из
    # pending_purchases), потом FSM. Порядок не важен — важно, что источников
    # два и переживший перезапуск бота лежит в базе.
    is_combo = bool(
        getattr(result, "is_combo", False) or fsm_data.get("combo_bypass_gb", 0) > 0
    )
    bypass_only_gb = fsm_data.get("bypass_only_gb", 0)

    # Fire-and-forget: создание или продление bypass-пользователя Remnawave.
    # Комбо сюда не идёт: ему трафик начислит grant_combo_traffic ниже.
    try:
        from app.services.remnawave_service import renew_remnawave_user_bg
        _sub_type = (tariff_type or "basic").strip().lower()
        if expires_at and _sub_type not in ("trial",) + config.BIZ_TARIFFS and not is_combo:
            renew_remnawave_user_bg(telegram_id, _sub_type, expires_at, period_days=period_days)
    except Exception as rmn_err:
        logger.warning("REMNAWAVE_HOOK_FAIL: stars tg=%s %s", telegram_id, rmn_err)

    # Комбо: гигабайты обхода. Исключение наружу не выпускаем — деньги взяты,
    # подписка выдана; отказ уже записан в лог внутри grant_combo_traffic.
    if is_combo:
        try:
            await grant_combo_traffic(
                telegram_id,
                tariff_type,
                period_days,
                is_combo=True,
                purchase_id=purchase_id,
                subscription_end=expires_at,
                source="telegram_stars" if env.is_stars_payment else "telegram_invoice",
            )
        except Exception as traffic_err:
            logger.error(
                "COMBO_TRAFFIC_UNEXPECTED user=%s purchase_id=%s: %s",
                telegram_id, purchase_id, traffic_err,
            )

    # Bypass-only: отдельный продукт (пакет ГБ без подписки). Объём приходит
    # из FSM и в COMBO_TARIFFS его нет — общей функции здесь делать нечего.
    if bypass_only_gb > 0:
        from app.services import remnawave_service
        try:
            rmn_success = await remnawave_service.add_bypass_traffic(
                telegram_id,
                bypass_only_gb * 1024**3,
                subscription_type=(tariff_type or "basic").strip().lower(),
                subscription_end=expires_at,
                period_days=period_days,
            )
            if rmn_success:
                await database.record_traffic_purchase(telegram_id, bypass_only_gb, 0)
                logger.info(
                    "BYPASS_ONLY_TRAFFIC_ADDED user=%s gb=%s", telegram_id, bypass_only_gb,
                )
            else:
                logger.error(
                    "BYPASS_ONLY_TRAFFIC_FAIL user=%s gb=%s — оплачено, не начислено",
                    telegram_id, bypass_only_gb,
                )
        except Exception as traffic_err:
            logger.error(
                "BYPASS_ONLY_TRAFFIC_ERROR user=%s gb=%s: %s",
                telegram_id, bypass_only_gb, traffic_err,
            )

        try:
            from app.services.trials import service as trial_service
            if await trial_service.is_trial_available(telegram_id):
                await trial_service.activate_trial(telegram_id)
                logger.info("BYPASS_TRIAL_ACTIVATED user=%s", telegram_id)
        except Exception as trial_err:
            logger.warning("BYPASS_TRIAL_FAIL user=%s: %s", telegram_id, trial_err)
