"""Жизненный цикл грядки: посадить, ухаживать, собрать, купить, выкопать.

ЧТО ЗДЕСЬ
    Все обработчики, которые не про шторм: главный экран фермы, выбор
    культуры, посадка, полив, удобрение, сбор урожая, уборка погибшего
    растения, покупка новой грядки, выкапывание и заглушка неактивных кнопок.

ПОЧЕМУ ОТДЕЛЬНО ОТ ШТОРМА
    Шторм (storm.py) — это деньги наружу: счета в Lava и СБП, запас времени
    до удара, споры о возвратах. Здесь деньги ходят только внутри баланса.
    Правят их по разным поводам, и смешивать их в одном файле означало
    трогать оплату плёнки при правке полива.

ДЕНЬГИ — ТОЛЬКО ЧЕРЕЗ АТОМАРНЫЕ ХЕЛПЕРЫ
    Сбор урожая идёт через database.harvest_plot_atomic, покупка грядки —
    через database.buy_farm_plot_atomic. Раньше и то и другое было тремя
    отдельными запросами: двойной клик начислял награду несколько раз, а
    сбой между запросами снимал деньги, не выдав грядку. Обходить эти
    функции нельзя.

ЧТО ЛЕГКО СЛОМАТЬ
    Посадка запрещена на время объявленного шторма, и проверка стоит ДВАЖДЫ —
    в callback_farm_choose_plant и в callback_farm_plant. Вторая не
    дублирование: callback_data можно собрать руками и обойти скрытое меню.

    Роутер этого модуля обязан быть подключён в app/handlers/farm/__init__.py.
    Забытый include_router не даёт никакой ошибки — кнопки просто молчат.
"""
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter

import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.utils import safe_edit_text

from app.handlers.game import (
    FARM_MAX_PLOTS,
    FARM_PLOT_PRICE_KOPECKS,
    PLANT_TYPES,
    get_games_back_keyboard,
)
from app.handlers.farm.mechanics import (
    _apply_growth_boost,
    _get_imminent_storm,
    _plant_name,
)
from app.handlers.farm.screen import _render_farm

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "game_farm")
async def callback_game_farm(callback: CallbackQuery):
    """Farm game main screen"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    await callback.answer()
    
    pool = await database.get_pool()
    if not pool:
        telegram_id = callback.from_user.id
        language = await resolve_user_language(telegram_id)
        await safe_edit_text(callback.message,
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
            parse_mode="HTML",
        )
        return
    
    await _render_farm(callback, pool)


@router.callback_query(F.data.startswith("farm_choose_"))
async def callback_farm_choose_plant(callback: CallbackQuery, state: FSMContext):
    """Show plant selection screen"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # During an announced storm planting is disabled to prevent the
    # replant + early-harvest loop and to keep the rule simple for players.
    if await _get_imminent_storm() is not None:
        await callback.answer(
            i18n_get_text(language, "farm.storm_planting_blocked"),
            show_alert=True,
        )
        return

    plot_id = int(callback.data.split("_")[-1])

    buttons = []
    for key, plant in PLANT_TYPES.items():
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(
                language, "farm.plant_info",
                emoji=plant["emoji"], name=_plant_name(language, key),
                days=plant["days"], reward=plant["reward"] // 100,
            ),
            callback_data=f"farm_plant_{plot_id}_{key}"
        )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "farm.back"), callback_data="game_farm",
    )])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await safe_edit_text(callback.message,
        i18n_get_text(language, "farm.choose_plant_title", num=plot_id + 1),
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("farm_plant_"))
async def callback_farm_plant(callback: CallbackQuery, state: FSMContext):
    """Plant a seed"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # Server-side gate — must match the farm_choose_ guard.  A user could
    # otherwise hand-craft farm_plant_<plot>_<type> to bypass the menu hide.
    if await _get_imminent_storm() is not None:
        await callback.answer(
            i18n_get_text(language, "farm.storm_planting_blocked"),
            show_alert=True,
        )
        return

    parts = callback.data.split("_")
    plot_id = int(parts[2])
    plant_type = parts[3]

    if plant_type not in PLANT_TYPES:
        await callback.answer(
            i18n_get_text(language, "farm.error_unknown_plant"), show_alert=True,
        )
        return
    
    pool = await database.get_pool()
    if not pool:
        await safe_edit_text(callback.message,
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
            parse_mode="HTML",
        )
        return
    
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    
    # Find plot
    plot = None
    for p in farm_plots:
        if p["plot_id"] == plot_id:
            plot = p
            break
    
    if not plot or plot["status"] != "empty":
        await callback.answer(
            i18n_get_text(language, "farm.error_plot_unavailable"), show_alert=True,
        )
        return

    now = datetime.now(timezone.utc)
    grow_seconds = PLANT_TYPES[plant_type]["days"] * 86400
    ready_at = now + timedelta(seconds=grow_seconds)
    dead_at = ready_at + timedelta(hours=24)
    
    plot["status"] = "growing"
    plot["plant_type"] = plant_type
    plot["planted_at"] = now.isoformat()
    plot["ready_at"] = ready_at.isoformat()
    plot["dead_at"] = dead_at.isoformat()
    plot["notified_ready"] = False
    plot["notified_12h"] = False
    plot["notified_dead"] = False
    plot["water_used_at"] = None
    plot["fertilizer_used_at"] = None
    
    await database.save_farm_plots(telegram_id, farm_plots)
    await _render_farm(callback, pool, farm_plots, plot_count, balance)


@router.callback_query(F.data.startswith("farm_water_"))
async def callback_farm_water(callback: CallbackQuery, state: FSMContext):
    """Water a plant"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    plot_id = int(callback.data.split("_")[-1])
    
    pool = await database.get_pool()
    if not pool:
        await safe_edit_text(callback.message,
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
            parse_mode="HTML",
        )
        return
    
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    
    plot = None
    for p in farm_plots:
        if p["plot_id"] == plot_id:
            plot = p
            break
    
    if not plot or plot["status"] != "growing":
        await callback.answer(
            i18n_get_text(language, "farm.error_plot_unavailable"), show_alert=True,
        )
        return

    now = datetime.now(timezone.utc)
    water_used = plot.get("water_used_at")
    if water_used:
        water_time = datetime.fromisoformat(water_used)
        if (now - water_time).total_seconds() < 86400:
            await callback.answer(
                i18n_get_text(language, "farm.error_already_watered"), show_alert=True,
            )
            return

    # Полив снимает 6 часов, но общий потолок ускорения считает _apply_growth_boost.
    # Если лимит выбран — суточный кулдаун не сжигаем: человек не виноват, что
    # растение уже на пределе, пусть попробует на другой грядке.
    if not _apply_growth_boost(plot, hours=6):
        await callback.answer(
            i18n_get_text(language, "farm.error_boost_limit"), show_alert=True,
        )
        return
    plot["water_used_at"] = now.isoformat()

    await database.save_farm_plots(telegram_id, farm_plots)
    await _render_farm(callback, pool, farm_plots, plot_count, balance)


@router.callback_query(F.data.startswith("farm_fert_"))
async def callback_farm_fert(callback: CallbackQuery, state: FSMContext):
    """Fertilize a plant"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    plot_id = int(callback.data.split("_")[-1])
    
    pool = await database.get_pool()
    if not pool:
        await safe_edit_text(callback.message,
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
            parse_mode="HTML",
        )
        return
    
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    
    plot = None
    for p in farm_plots:
        if p["plot_id"] == plot_id:
            plot = p
            break
    
    if not plot or plot["status"] != "growing":
        await callback.answer(
            i18n_get_text(language, "farm.error_plot_unavailable"), show_alert=True,
        )
        return

    now = datetime.now(timezone.utc)
    fert_used = plot.get("fertilizer_used_at")
    if fert_used:
        fert_time = datetime.fromisoformat(fert_used)
        if (now - fert_time).total_seconds() < 86400:
            await callback.answer(
                i18n_get_text(language, "farm.error_already_fertilized"), show_alert=True,
            )
            return

    # Удобрение снимает 2 часа — тот же общий потолок, что и у полива.
    if not _apply_growth_boost(plot, hours=2):
        await callback.answer(
            i18n_get_text(language, "farm.error_boost_limit"), show_alert=True,
        )
        return
    plot["fertilizer_used_at"] = now.isoformat()

    await database.save_farm_plots(telegram_id, farm_plots)
    await _render_farm(callback, pool, farm_plots, plot_count, balance)


@router.callback_query(F.data.startswith("farm_harvest_"))
async def callback_farm_harvest(callback: CallbackQuery, state: FSMContext):
    """Harvest a ready plant"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    plot_id = int(callback.data.split("_")[-1])
    
    pool = await database.get_pool()
    if not pool:
        await safe_edit_text(callback.message,
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
            parse_mode="HTML",
        )
        return
    
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    
    plot = None
    for p in farm_plots:
        if p["plot_id"] == plot_id:
            plot = p
            break
    
    if not plot or plot["status"] != "ready":
        await callback.answer(
            i18n_get_text(language, "farm.error_not_ready"), show_alert=True,
        )
        return

    plant_type = plot.get("plant_type")
    plant = PLANT_TYPES.get(plant_type, {})
    reward_kopecks = plant.get("reward", 0)
    reward_rubles = reward_kopecks / 100.0

    # Сброс грядки и начисление — одной транзакцией под advisory-локом.
    # Раньше это были три отдельные операции, и двойной клик начислял награду
    # несколько раз: обе копии обработчика успевали пройти проверку статуса.
    success, reason = await database.harvest_plot_atomic(
        telegram_id=telegram_id,
        plot_id=plot_id,
        reward_kopecks=int(reward_kopecks),
        source="farm_harvest",
        description=f"Farm harvest: {plant.get('name', 'unknown')}",
    )

    if not success:
        if reason == "plot_wrong_status":
            # Сюда попадает второй клик по той же грядке — это не ошибка.
            await callback.answer(
                i18n_get_text(language, "farm.error_already_harvested"), show_alert=True,
            )
        else:
            logger.warning(
                "FARM_HARVEST_FAILED user=%s plot=%s reason=%s",
                telegram_id, plot_id, reason,
            )
            await callback.answer(
                i18n_get_text(language, "farm.error_harvest_failed"), show_alert=True,
            )
        return

    # Refresh balance
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    await _render_farm(callback, pool, farm_plots, plot_count, balance)

    await callback.answer(
        i18n_get_text(language, "farm.harvest_success", reward=f"{reward_rubles:.0f}"),
        show_alert=True,
    )


@router.callback_query(F.data.startswith("farm_remove_"))
async def callback_farm_remove(callback: CallbackQuery, state: FSMContext):
    """Remove dead plant - show confirmation"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    plot_id = int(callback.data.split("_")[-1])
    
    # Check if this is a confirmation
    if callback.data.startswith("farm_remove_confirm_"):
        plot_id = int(callback.data.split("_")[-1])
        
        pool = await database.get_pool()
        if not pool:
            await safe_edit_text(callback.message,
                i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
                reply_markup=get_games_back_keyboard(language),
                parse_mode="HTML",
            )
            return
        
        farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
        
        plot = None
        for p in farm_plots:
            if p["plot_id"] == plot_id:
                plot = p
                break
        
        if plot and plot["status"] == "dead":
            # Reset plot
            plot["status"] = "empty"
            plot["plant_type"] = None
            plot["planted_at"] = None
            plot["ready_at"] = None
            plot["dead_at"] = None
            plot["notified_ready"] = False
            plot["notified_12h"] = False
            plot["notified_dead"] = False
            plot["water_used_at"] = None
            plot["fertilizer_used_at"] = None
            
            await database.save_farm_plots(telegram_id, farm_plots)
            await _render_farm(callback, pool, farm_plots, plot_count, balance)
        return
    
    # Show confirmation dialog
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "farm.remove_yes"),
            callback_data=f"farm_remove_confirm_{plot_id}"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "farm.remove_no"),
            callback_data="farm_noop"
        )]
    ])

    await safe_edit_text(callback.message,
        i18n_get_text(language, "farm.remove_confirm"),
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "farm_buy_plot")
async def callback_farm_buy_plot(callback: CallbackQuery, state: FSMContext):
    """Buy a new plot"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    pool = await database.get_pool()
    if not pool:
        await safe_edit_text(callback.message,
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
            parse_mode="HTML",
        )
        return
    
    # Покупка грядки одной транзакцией: списание, грядка и счётчик вместе.
    # Раньше это были три отдельных запроса — сбой между ними снимал деньги
    # без грядки, а параллельные клики создавали две грядки с одним номером.
    ok, reason = await database.buy_farm_plot_atomic(
        telegram_id,
        price_kopecks=FARM_PLOT_PRICE_KOPECKS,
        max_plots=FARM_MAX_PLOTS,
        description=f"Покупка грядки за {FARM_PLOT_PRICE_KOPECKS // 100} ₽",
    )
    if not ok:
        # Отказ объясняем текстом, а не молчанием: «максимум грядок» и
        # «не хватает денег» — разные истории для человека.
        messages = {
            "max_plots_reached": "farm.max_plots_reached",
            "insufficient_balance": "farm.insufficient_funds",
        }
        await callback.answer(
            i18n_get_text(language, messages.get(reason, "farm.buy_plot_error")),
            show_alert=True,
        )
        if reason not in messages:
            logger.warning(
                "FARM_BUY_PLOT_FAILED user=%s reason=%s", telegram_id, reason
            )
        return

    # Refresh balance
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    await _render_farm(callback, pool, farm_plots, plot_count, balance)


@router.callback_query(F.data.startswith("farm_dig_") & ~F.data.startswith("farm_dig_confirm_"), StateFilter("*"))
async def callback_farm_dig(callback: CallbackQuery, state: FSMContext):
    """Show confirmation dialog for digging up a plant"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    plot_id = int(callback.data.split("_")[-1])
    
    pool = await database.get_pool()
    if not pool:
        await safe_edit_text(callback.message,
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
            parse_mode="HTML",
        )
        return
    
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    plot = next((p for p in farm_plots if p["plot_id"] == plot_id), None)
    
    if not plot or plot["status"] != "growing":
        await callback.answer(
            i18n_get_text(language, "farm.dig_unavailable"), show_alert=True,
        )
        return

    plant_name = _plant_name(language, plot.get("plant_type", ""))

    # Show confirmation with inline keyboard
    confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=i18n_get_text(language, "farm.dig_yes"),
                callback_data=f"farm_dig_confirm_{plot_id}"
            ),
            InlineKeyboardButton(
                text=i18n_get_text(language, "farm.remove_no"),
                callback_data="game_farm"
            )
        ]
    ])

    await safe_edit_text(callback.message,
        i18n_get_text(language, "farm.dig_confirm", name=plant_name, num=plot_id + 1),
        reply_markup=confirm_keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("farm_dig_confirm_"), StateFilter("*"))
async def callback_farm_dig_confirm(callback: CallbackQuery, state: FSMContext):
    """Confirm and execute digging up a plant"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    plot_id = int(callback.data.split("_")[-1])
    
    pool = await database.get_pool()
    if not pool:
        await safe_edit_text(callback.message,
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
            parse_mode="HTML",
        )
        return
    
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    plot = next((p for p in farm_plots if p["plot_id"] == plot_id), None)
    
    if not plot:
        await callback.answer(
            i18n_get_text(language, "farm.error_plot_not_found"), show_alert=True,
        )
        return
    
    # Reset plot to empty
    plot["status"] = "empty"
    plot["plant_type"] = None
    plot["planted_at"] = None
    plot["ready_at"] = None
    plot["dead_at"] = None
    plot["notified_ready"] = False
    plot["notified_12h"] = False
    plot["notified_dead"] = False
    plot["water_used_at"] = None
    plot["fertilizer_used_at"] = None
    
    await database.save_farm_plots(telegram_id, farm_plots)
    await callback.answer(
        i18n_get_text(language, "farm.dig_success"), show_alert=True,
    )
    await _render_farm(callback, pool, farm_plots=farm_plots, 
                       plot_count=plot_count, balance=balance)


@router.callback_query(F.data == "farm_noop")
async def callback_farm_noop(callback: CallbackQuery):
    """No-op handler for disabled buttons"""
    await callback.answer()
