"""Шторм: плёнка на грядку и ранний сбор — единственные способы спастись.

ЧТО ЗДЕСЬ
    Обработчики окна объявленного шторма: покупка плёнки с баланса, счёт в
    Lava (карта) и в Платеге (СБП), а также ранний сбор за половину награды.

ПОЧЕМУ ОТДЕЛЬНО ОТ ГРЯДОК
    Здесь деньги уходят наружу и возвращаются вебхуком. Отсюда своя логика,
    которой больше нигде в ферме нет: запас времени до удара, повторная
    проверка перед созданием счёта, надбавка СБП. Всё это правят по поводам
    платёжных провайдеров, а не игровой механики.

ЗАПАС ВРЕМЕНИ — ЭТО ПРО ДЕНЬГИ, А НЕ ПРО УДОБСТВО
    Счёт на плёнку нельзя выставить, если до удара меньше
    SHIELD_INVOICE_MIN_LEAD_MINUTES: платёж не успеет дойти, шторм отработает
    раньше вебхука, грядка погибнет — а деньги уже ушли. Дальше это разбирает
    поддержка вручную. Оплату С БАЛАНСА это не касается: она мгновенная и
    остаётся доступной до самого удара, запрещать её нельзя.

ПРОВЕРКА ПОВТОРЯЕТСЯ ПЕРЕД КАЖДЫМ СЧЁТОМ
    Экран с кнопками «Картой»/«СБП» остаётся в чате и через час, когда шторм
    уже на пороге или прошёл. Поэтому _shield_invoice_allowed зовут и в
    callback_farm_shield_lava, и в callback_farm_shield_sbp, а не только на
    экране выбора оплаты.

ЧТО ЛЕГКО СЛОМАТЬ
    Счёт всегда на ПОЛНУЮ стоимость плёнки. Комбинированной оплаты
    (баланс + карта) в проекте нет, и баланс при внешнем платеже не
    трогается. Написать на экране «не хватает N ₽» — прямой повод для спора
    о деньгах: человек прочитает N, а счёт придёт на полную сумму.

    Роутер этого модуля обязан быть подключён в app/handlers/farm/__init__.py.
    Забытый include_router не даёт никакой ошибки — кнопки просто молчат.
"""
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.utils import safe_edit_text

from app.handlers.game import (
    PLANT_TYPES,
    farm_half_reward_kopecks,
    format_kopecks_rub,
    storm_shield_price_kopecks,
)
from app.handlers.farm.mechanics import (
    SHIELD_INVOICE_MIN_LEAD_MINUTES,
    _get_imminent_storm,
    _invoice_can_arrive_in_time,
    _plant_name,
    _storm_seconds_left,
)
from app.handlers.farm.screen import _render_farm

router = Router()
logger = logging.getLogger(__name__)


def _parse_plot_id(callback_data: str, prefix: str) -> int:
    """Extract integer plot_id from 'prefix:<n>' callback data; -1 on parse fail."""
    try:
        return int(callback_data.split(":", 1)[1])
    except (ValueError, IndexError):
        return -1


async def _find_growing_plot(telegram_id: int, plot_id: int):
    """Return (farm_plots, plot_count, balance, plot_dict) or (..., None) if
    the plot is missing / not growing.  Caller short-circuits."""
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    target = None
    for p in farm_plots:
        if int(p.get("plot_id", -1)) == plot_id:
            target = p
            break
    if target is None or target.get("status") != "growing":
        return farm_plots, plot_count, balance, None
    return farm_plots, plot_count, balance, target


async def _shield_invoice_allowed(callback, language: str, telegram_id: int, plot_id: int) -> bool:
    """Можно ли сейчас выставлять счёт на плёнку. Отказ объясняет сам.

    Проверка повторяется перед каждым созданием счёта, а не только на экране
    выбора оплаты: сообщение с кнопками «Картой»/«СБП» остаётся в чате и через
    час, когда шторм уже на пороге или прошёл.
    """
    storm = await _get_imminent_storm()
    if storm is None:
        await callback.answer(
            i18n_get_text(language, "farm.shield_no_storm"), show_alert=True,
        )
        return False
    if not _invoice_can_arrive_in_time(storm):
        logger.info(
            "FARM_SHIELD_TOO_LATE user=%s plot=%s seconds_left=%.0f",
            telegram_id, plot_id, _storm_seconds_left(storm),
        )
        await callback.answer(
            i18n_get_text(
                language, "farm.shield_invoice_too_late",
                minutes=SHIELD_INVOICE_MIN_LEAD_MINUTES,
            ),
            show_alert=True,
        )
        return False
    return True


@router.callback_query(F.data.startswith("farm_shield:"))
async def callback_farm_shield(callback: CallbackQuery):
    """🛡 Накрыть — pay via balance if enough, else show Lava/SBP screen."""
    if not await ensure_db_ready_callback(callback):
        return
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    storm = await _get_imminent_storm()
    if storm is None:
        await callback.answer(
            i18n_get_text(language, "farm.shield_no_storm"), show_alert=True,
        )
        return

    plot_id = _parse_plot_id(callback.data, "farm_shield")
    if plot_id < 0:
        return
    farm_plots, plot_count, balance, plot = await _find_growing_plot(telegram_id, plot_id)
    if plot is None:
        await callback.answer(
            i18n_get_text(language, "farm.shield_plot_not_growing"), show_alert=True,
        )
        return
    if plot.get("storm_shielded"):
        await callback.answer(
            i18n_get_text(language, "farm.shield_already"), show_alert=True,
        )
        return

    plant = PLANT_TYPES.get(plot.get("plant_type"), {})
    shield_cost = storm_shield_price_kopecks(int(plant.get("reward", 0)))
    shield_cost_rub = shield_cost // 100

    if balance >= shield_cost:
        ok, reason = await database.apply_storm_shield_atomic(
            telegram_id, plot_id, shield_cost, deduct_balance=True,
        )
        if ok:
            await callback.answer(
                i18n_get_text(language, "farm.shield_applied", price=shield_cost_rub),
                show_alert=True,
            )
        else:
            # Внутреннюю причину отказа показывать человеку нечего — она
            # техническая ("plot_not_growing"), да ещё и по-английски.
            logger.info(
                "FARM_SHIELD_BALANCE_FAILED user=%s plot=%s reason=%s",
                telegram_id, plot_id, reason,
            )
            await callback.answer(
                i18n_get_text(language, "farm.shield_failed"), show_alert=True,
            )
        pool = await database.get_pool()
        await _render_farm(callback, pool)
        return

    # Денег на балансе не хватает — остаётся внешняя оплата. Но если до удара
    # меньше SHIELD_INVOICE_MIN_LEAD_MINUTES, счёт выставлять нельзя: платёж
    # не успеет дойти, грядка погибнет, а деньги уже уйдут — и дальше это
    # разбирает поддержка вручную.
    if not _invoice_can_arrive_in_time(storm):
        logger.info(
            "FARM_SHIELD_TOO_LATE user=%s plot=%s seconds_left=%.0f",
            telegram_id, plot_id, _storm_seconds_left(storm),
        )
        await callback.answer(
            i18n_get_text(
                language, "farm.shield_invoice_too_late",
                minutes=SHIELD_INVOICE_MIN_LEAD_MINUTES,
            ),
            show_alert=True,
        )
        return

    # Экран оплаты. Счёт всегда на ПОЛНУЮ стоимость плёнки: комбинированной
    # оплаты (баланс + карта) в проекте нет, а баланс при внешнем платеже не
    # трогается. Раньше здесь писали «не хватает N ₽», человек жал «Картой» и
    # получал счёт на всю сумму — прямой повод для спора о деньгах.
    text = i18n_get_text(
        language, "farm.shield_payment_title",
        num=plot_id + 1,
        emoji=plant.get("emoji", ""),
        name=_plant_name(language, plot.get("plant_type")),
        price=shield_cost_rub,
        balance=f"{balance / 100:.2f}",
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "farm.pay_card"),
            callback_data=f"farm_shield_lava:{plot_id}",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "farm.pay_sbp"),
            callback_data=f"farm_shield_sbp:{plot_id}",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "farm.back_to_farm"),
            callback_data="game_farm",
        )],
    ])
    try:
        await safe_edit_text(callback.message,text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise

    # Экран показан — гасим «часики». Ответ ровно один: пути с отказом
    # выше уже ответили своим алертом и вышли (см. _render_farm).
    try:
        await callback.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("farm_shield_lava:"))
async def callback_farm_shield_lava(callback: CallbackQuery):
    """Pay shield via Lava (card)."""
    if not await ensure_db_ready_callback(callback):
        return
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    plot_id = _parse_plot_id(callback.data, "farm_shield_lava")
    if plot_id < 0:
        return
    # Экран оплаты живёт в чате и после того, как ситуация изменилась, —
    # проверяем запас времени ещё раз, уже перед выставлением счёта.
    if not await _shield_invoice_allowed(callback, language, telegram_id, plot_id):
        return
    _, _, _, plot = await _find_growing_plot(telegram_id, plot_id)
    if plot is None:
        await callback.answer(
            i18n_get_text(language, "farm.shield_plot_not_growing"), show_alert=True,
        )
        return
    plant = PLANT_TYPES.get(plot.get("plant_type"), {})
    shield_cost = storm_shield_price_kopecks(int(plant.get("reward", 0)))

    import lava_service
    if not lava_service.is_enabled():
        await callback.answer(
            i18n_get_text(language, "farm.pay_card_unavailable"), show_alert=True,
        )
        return

    try:
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff="farm_storm_shield",
            period_days=0,
            price_kopecks=shield_cost,
            purchase_type="farm_effect",
            farm_plot_id=plot_id,
        )
        invoice = await lava_service.create_invoice(
            amount_rubles=shield_cost / 100.0,
            purchase_id=purchase_id,
            comment=i18n_get_text(
                language, "farm.shield_invoice_comment", num=plot_id + 1,
            ),
        )
        invoice_id = invoice["invoice_id"]
        payment_url = invoice["payment_url"]
        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(invoice_id))
        except Exception as e:
            logger.error("Failed to save Lava invoice_id: %s", e)

        text = i18n_get_text(
            language, "farm.shield_lava_invoice", amount=shield_cost // 100,
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "farm.shield_lava_button"), url=payment_url,
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "farm.back_to_farm"), callback_data="game_farm",
            )],
        ])
        await safe_edit_text(callback.message,text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.exception("FARM_SHIELD_LAVA_ERROR user=%s plot=%s: %s", telegram_id, plot_id, e)
        await callback.answer(
            i18n_get_text(language, "farm.pay_error"), show_alert=True,
        )


@router.callback_query(F.data.startswith("farm_shield_sbp:"))
async def callback_farm_shield_sbp(callback: CallbackQuery):
    """Pay shield via Платега (SBP, +11%)."""
    if not await ensure_db_ready_callback(callback):
        return
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    plot_id = _parse_plot_id(callback.data, "farm_shield_sbp")
    if plot_id < 0:
        return
    # См. комментарий в оплате картой: запас времени проверяем и здесь.
    if not await _shield_invoice_allowed(callback, language, telegram_id, plot_id):
        return
    _, _, _, plot = await _find_growing_plot(telegram_id, plot_id)
    if plot is None:
        await callback.answer(
            i18n_get_text(language, "farm.shield_plot_not_growing"), show_alert=True,
        )
        return
    plant = PLANT_TYPES.get(plot.get("plant_type"), {})
    shield_cost = storm_shield_price_kopecks(int(plant.get("reward", 0)))

    import platega_service
    if not platega_service.is_enabled():
        await callback.answer(
            i18n_get_text(language, "farm.pay_sbp_unavailable"), show_alert=True,
        )
        return

    try:
        sbp_kopecks = platega_service.apply_sbp_markup(shield_cost)
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff="farm_storm_shield",
            period_days=0,
            price_kopecks=sbp_kopecks,
            purchase_type="farm_effect",
            farm_plot_id=plot_id,
        )
        tx = await platega_service.create_transaction(
            amount_rubles=sbp_kopecks / 100.0,
            description=i18n_get_text(
                language, "farm.shield_invoice_comment", num=plot_id + 1,
            ),
            purchase_id=purchase_id,
        )
        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(tx["transaction_id"]))
        except Exception as e:
            logger.error("Failed to save SBP tx_id: %s", e)

        text = i18n_get_text(
            language, "farm.shield_sbp_invoice", amount=f"{sbp_kopecks / 100:.2f}",
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "farm.shield_sbp_button"),
                url=tx["redirect_url"],
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "farm.back_to_farm"), callback_data="game_farm",
            )],
        ])
        await safe_edit_text(callback.message,text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.exception("FARM_SHIELD_SBP_ERROR user=%s plot=%s: %s", telegram_id, plot_id, e)
        await callback.answer(
            i18n_get_text(language, "farm.pay_error"), show_alert=True,
        )


@router.callback_query(F.data.startswith("farm_early:"))
async def callback_farm_early_harvest(callback: CallbackQuery):
    """🚜 Собрать незрелым — credits 50% of plant reward, frees the plot."""
    if not await ensure_db_ready_callback(callback):
        return
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    storm = await _get_imminent_storm()
    if storm is None:
        await callback.answer(
            i18n_get_text(language, "farm.early_no_storm"), show_alert=True,
        )
        return

    plot_id = _parse_plot_id(callback.data, "farm_early")
    if plot_id < 0:
        return
    farm_plots, plot_count, balance, plot = await _find_growing_plot(telegram_id, plot_id)
    if plot is None:
        await callback.answer(
            i18n_get_text(language, "farm.shield_plot_not_growing"), show_alert=True,
        )
        return

    plant = PLANT_TYPES.get(plot.get("plant_type"), {})
    # Половину считает farm_half_reward_kopecks и только она — та же функция
    # стоит за суммой на кнопке в screen.py. Пока формул было две (здесь
    # reward // 2 в копейках, на кнопке reward // 200 сразу в рублях), витрина
    # и касса расходились на 50 копеек у каждой нечётной награды.
    half_reward_kopecks = farm_half_reward_kopecks(int(plant.get("reward", 0)))
    if half_reward_kopecks <= 0:
        await callback.answer(
            i18n_get_text(language, "farm.early_unavailable"), show_alert=True,
        )
        return

    # Сброс грядки и начисление — одной транзакцией под advisory-локом, иначе
    # двойной клик по «собрать незрелым» начисляет половину награды дважды.
    ok, reason = await database.harvest_plot_atomic(
        telegram_id=telegram_id,
        plot_id=plot_id,
        reward_kopecks=half_reward_kopecks,
        expected_status="growing",
        source="farm_early_harvest",
        description=f"Early harvest plot {plot_id} ({plant.get('name','')})",
    )
    if not ok:
        if reason == "plot_wrong_status":
            await callback.answer(
                i18n_get_text(language, "farm.error_already_harvested"), show_alert=True,
            )
        else:
            logger.warning(
                "FARM_EARLY_HARVEST_FAILED user=%s plot=%s reason=%s",
                telegram_id, plot_id, reason,
            )
            await callback.answer(
                i18n_get_text(language, "farm.early_failed"), show_alert=True,
            )
        return

    await callback.answer(
        i18n_get_text(
            language, "farm.early_success",
            emoji=plant.get("emoji", ""),
            # Показываем ровно то, что ушло на баланс, с копейками: «26.50»,
            # а не «26». Раньше здесь делили на 100 и человек видел сумму
            # меньше зачисленной.
            reward=format_kopecks_rub(half_reward_kopecks),
        ),
        show_alert=True,
    )
    pool = await database.get_pool()
    await _render_farm(callback, pool)
