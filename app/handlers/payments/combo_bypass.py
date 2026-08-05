"""Комбо и обход: гигабайты Remnawave поверх оплаченной подписки.

ЧТО ЗДЕСЬ
    Одна функция — начисление трафика обхода после успешной оплаты и
    продление пользователя Remnawave для обычных подписок.

ПОЧЕМУ ВЫДЕЛЕНО
    Это единственная часть обработчика, которая ходит во внешнюю панель, и
    единственная, которая восстанавливает потерянные данные из конфига.
    Смешивать её с вёрсткой экрана успеха незачем: ломается и правится она
    по своим поводам (доступность Remnawave, состав COMBO_TARIFFS).

ЧТО ЛЕГКО СЛОМАТЬ
    Восстановление объёма ГБ из конфига. combo_bypass_gb кладётся в FSM при
    выставлении счёта, но между счётом и оплатой человек успевает открыть
    другое меню (FSM очищается) или бот перезапускается (FSM в памяти
    теряется). Уберёте fallback по config.COMBO_TARIFFS — покупатели комбо
    получат подписку и НОЛЬ гигабайт обхода, без единой ошибки в логах.

    Взаимоисключение веток: обычное продление Remnawave делается только
    когда гигабайтов нет (combo_bypass_gb <= 0), иначе трафик начислит
    add_bypass_traffic. Позовёте оба — продление затрёт начисление.

    Ни одно исключение отсюда не должно всплывать наверх: деньги уже взяты,
    подписка уже выдана. Поэтому каждый блок в своём try.
"""
import logging

import config
import database
from aiogram.fsm.context import FSMContext

from app.handlers.payments.payment_preflight import PaymentEnvelope, PurchaseContext
from app.handlers.payments.subscription_finalize import FinalizedSubscription

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

    # Fire-and-forget: create or renew Remnawave bypass user
    # Skip for combo purchases — combo traffic is added separately below
    fsm_data = await state.get_data()
    combo_bypass_gb = fsm_data.get("combo_bypass_gb", 0)

    # CRITICAL FSM-FALLBACK: combo_bypass_gb is set in FSM state when the
    # invoice is created, but Telegram Payments are asynchronous — between
    # invoice and SUCCESSFUL_PAYMENT the user can open another menu (which
    # state.clear()s), or the bot can restart (in-memory FSM is gone).
    # If we lost the FSM but finalize tells us this was a combo, recover
    # the GB amount from config.COMBO_TARIFFS by tariff + period_days.
    # Without this, combo Юкасса-buyers got their subscription but NO bypass GB.
    if combo_bypass_gb <= 0 and getattr(result, "is_combo", False):
        _sub_type_for_combo = (
            getattr(result, "subscription_type", None)
            or (tariff_type or "basic")
        ).strip().lower()
        combo_key = f"combo_{_sub_type_for_combo}"
        combo_info = (config.COMBO_TARIFFS or {}).get(combo_key, {}).get(period_days)
        if combo_info and combo_info.get("gb"):
            combo_bypass_gb = int(combo_info["gb"])
            logger.warning(
                "COMBO_BYPASS_FSM_FALLBACK user=%s gb=%s combo_key=%s period_days=%s "
                "purchase_id=%s — FSM was empty, recovered from config",
                telegram_id, combo_bypass_gb, combo_key, period_days, purchase_id,
            )
        else:
            logger.error(
                "COMBO_BYPASS_FSM_FALLBACK_FAIL user=%s combo_key=%s period_days=%s "
                "purchase_id=%s — combo config missing, GB cannot be granted",
                telegram_id, combo_key, period_days, purchase_id,
            )

    try:
        from app.services.remnawave_service import renew_remnawave_user_bg
        _sub_type = (tariff_type or "basic").strip().lower()
        if expires_at and _sub_type not in ("trial",) + config.BIZ_TARIFFS and combo_bypass_gb <= 0:
            renew_remnawave_user_bg(telegram_id, _sub_type, expires_at, period_days=period_days)
    except Exception as rmn_err:
        logger.warning("REMNAWAVE_HOOK_FAIL: stars tg=%s %s", telegram_id, rmn_err)

    # Combo/Bypass: начисляем трафик обхода если покупка была через комбо или bypass-only
    bypass_only_gb = fsm_data.get("bypass_only_gb", 0)

    if combo_bypass_gb > 0 or bypass_only_gb > 0:
        from app.services import remnawave_service
        gb = combo_bypass_gb or bypass_only_gb
        traffic_bytes = gb * 1024**3

        try:
            rmn_success = await remnawave_service.add_bypass_traffic(
                telegram_id,
                traffic_bytes,
                subscription_type=(tariff_type or "basic").strip().lower(),
                subscription_end=expires_at,
                period_days=period_days,
            )
            if not rmn_success:
                logger.warning(f"COMBO_BYPASS_TRAFFIC_FAIL user={telegram_id} gb={gb}")
            await database.record_traffic_purchase(telegram_id, gb, 0)
            logger.info(f"COMBO_BYPASS_TRAFFIC_ADDED user={telegram_id} gb={gb} type={'combo' if combo_bypass_gb else 'bypass_only'}")
        except Exception as traffic_err:
            logger.warning(f"COMBO_BYPASS_TRAFFIC_ERROR user={telegram_id}: {traffic_err}")

        # Mark subscription as combo (OUTSIDE traffic try block)
        if combo_bypass_gb > 0:
            try:
                await database.set_combo_flag(telegram_id, True)
                logger.info(f"COMBO_FLAG_SET user={telegram_id}")
            except Exception as flag_err:
                logger.warning(f"COMBO_FLAG_FAIL user={telegram_id}: {flag_err}")

        # Bypass-only: activate 3-day trial if eligible
        if bypass_only_gb > 0:
            try:
                from app.services.trials import service as trial_service
                if await trial_service.is_trial_available(telegram_id):
                    await trial_service.activate_trial(telegram_id)
                    logger.info(f"BYPASS_TRIAL_ACTIVATED user={telegram_id}")
            except Exception:
                pass
