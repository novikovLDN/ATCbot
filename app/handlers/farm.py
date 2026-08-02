"""Мини-игра «Ферма»: посадка, уход, сбор урожая и штормы.

КАК УСТРОЕНА ИГРА
    У пользователя есть грядки. На грядку сажают растение, поливают и
    удобряют (это ускоряет созревание), а созревшее собирают — награда
    зачисляется на баланс в рублях.

ПОЧЕМУ ЗДЕСЬ СТОЛЬКО АТОМАРНОСТИ
    Награда — реальные деньги на балансе, поэтому любое действие, которое
    начисляет или списывает, обязано выполняться одной транзакцией под
    advisory-локом. Раньше сбор урожая читал состояние, начислял награду и
    только потом сбрасывал грядку тремя отдельными запросами — двойной клик
    начислял награду несколько раз. Теперь этим занимается
    database.harvest_plot_atomic, и обходить её нельзя.

ШТОРМ
    Периодическое событие, уничтожающее незащищённые растения. Действует
    одинаково на всех, независимо от того, был ли игрок в боте: раньше
    офлайновые получали автосбор половины награды, из-за чего заходить в
    бота во время шторма было невыгодно. Спастись можно щитом или ранним
    сбором — оба действия требуют присутствия.

ЧТО ЛЕГКО СЛОМАТЬ
    Награды растений и цена щита заданы в копейках. Любая новая механика,
    выдающая ценность, обязана проходить через месячный потолок
    config.GAME_MONTHLY_DAYS_CAP, если выдаёт дни подписки.
"""
import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Set

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter
from aiogram.exceptions import TelegramBadRequest

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.keyboards import get_back_keyboard
from app.handlers.common.states import BomberState
from app.handlers.common.utils import safe_edit_text

router = Router()
logger = logging.getLogger(__name__)

# Общие с игровым меню элементы импортируются из game.py: клавиатура возврата,
# справочник растений, цены грядки и щита. Держать их там правильно — меню игр
# живёт в game.py и ссылается на те же значения.
from app.handlers.game import (  # noqa: E402
    FARM_MAX_PLOTS,
    FARM_PLOT_PRICE_KOPECKS,
    PLANT_TYPES,
    get_games_back_keyboard,
    storm_shield_price_kopecks,
)


async def _get_imminent_storm():
    """Return the storm row if announced & not executed, else None."""
    storm = await database.get_pending_storm()
    if not storm:
        return None
    if storm.get("announced_at") and not storm.get("executed_at"):
        return storm
    return None


async def _render_farm(callback, pool, farm_plots=None, plot_count=None, balance=None):
    """Render farm screen with current state"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    if farm_plots is None:
        farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    
    now = datetime.now(timezone.utc)
    
    # Sync statuses
    changed = False
    for plot in farm_plots:
        if plot["status"] == "growing" and plot.get("ready_at"):
            ready_at = datetime.fromisoformat(plot["ready_at"])
            if now >= ready_at:
                plot["status"] = "ready"
                changed = True
        if plot["status"] == "ready" and plot.get("dead_at"):
            dead_at = datetime.fromisoformat(plot["dead_at"])
            if now >= dead_at:
                plot["status"] = "dead"
                changed = True
    if changed:
        await database.save_farm_plots(telegram_id, farm_plots)
    
    # Imminent storm banner (only during the 24h announcement window)
    storm = await _get_imminent_storm()
    storm_active = storm is not None
    if storm_active:
        scheduled_at = storm["scheduled_at"]
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
        eta = scheduled_at - now
        eta_h = max(0, int(eta.total_seconds() // 3600))
        storm_banner = (
            f"⛈ <b>Надвигается шторм!</b> До удара ≈ {eta_h} ч\n"
            f"Растущие грядки без плёнки погибнут.\n"
            f"🚫 <b>Посадка новых растений недоступна до конца шторма.</b>\n"
        )
    else:
        storm_banner = None

    # Build text (plot 0 always visible; plots 1-8 only if purchased, i.e. plot_id < plot_count)
    lines = ["🌾 <b>Ваша ферма</b>\n"]
    if storm_banner:
        lines.append(storm_banner)
    for plot in farm_plots:
        if plot["plot_id"] >= plot_count:
            continue
        i = plot["plot_id"]
        status = plot["status"]
        pt = plot.get("plant_type")
        plant = PLANT_TYPES.get(pt, {}) if pt else {}
        
        if status == "empty":
            lines.append(f"Грядка {i+1}: ⬜ Пусто")
        elif status == "growing":
            ready_at = datetime.fromisoformat(plot["ready_at"])
            remaining = ready_at - now
            days = remaining.days
            hours = remaining.seconds // 3600
            shield_mark = " 🛡" if plot.get("storm_shielded") else ""
            lines.append(f"Грядка {i+1}: 🌱 {plant.get('name','')}{shield_mark} — осталось {days}д {hours}ч")
        elif status == "ready":
            lines.append(f"Грядка {i+1}: {plant.get('emoji','🌿')} {plant.get('name','')} — ✅ Готово к сбору!")
        elif status == "dead":
            lines.append(f"Грядка {i+1}: ☠️ {plant.get('name','')} — сгнило")
    
    lines.append(f"\n💰 Баланс: {balance/100:.2f} ₽")
    text = "\n".join(lines)
    
    # Build keyboard (same visibility: plot_id < plot_count)
    buttons = []
    for plot in farm_plots:
        if plot["plot_id"] >= plot_count:
            continue
        i = plot["plot_id"]
        status = plot["status"]
        pt = plot.get("plant_type")
        plant = PLANT_TYPES.get(pt, {}) if pt else {}
        
        if status == "empty":
            if storm_active:
                buttons.append([InlineKeyboardButton(
                    text=f"🚫 Грядка {i+1}: посадка во время шторма недоступна",
                    callback_data="farm_noop"
                )])
            else:
                buttons.append([InlineKeyboardButton(
                    text=f"🌱 Посадить на грядку {i+1}",
                    callback_data=f"farm_choose_{i}"
                )])
        elif status == "growing":
            # Storm controls — only during the 24h announcement window, only if not already shielded.
            # Planting is disabled during a storm (see callback_farm_choose_plant), so every
            # growing plot at this point was planted BEFORE the storm — no replant exploit possible.
            if storm_active and not plot.get("storm_shielded"):
                shield_cost_kopecks = storm_shield_price_kopecks(int(plant.get("reward", 0)))
                shield_cost_rub = shield_cost_kopecks // 100
                half_reward_rub = int(plant.get("reward", 0)) // 200  # half of reward, in RUB
                buttons.append([InlineKeyboardButton(
                    text=f"🛡 Накрыть #{i+1} — {shield_cost_rub} ₽",
                    callback_data=f"farm_shield:{i}"
                )])
                buttons.append([InlineKeyboardButton(
                    text=f"🚜 Собрать незрелым #{i+1} — +{half_reward_rub} ₽",
                    callback_data=f"farm_early:{i}"
                )])

            # Water button
            row = []
            water_used = plot.get("water_used_at")
            can_water = not water_used or (now - datetime.fromisoformat(water_used)).total_seconds() >= 86400
            fert_used = plot.get("fertilizer_used_at")
            can_fert = not fert_used or (now - datetime.fromisoformat(fert_used)).total_seconds() >= 86400

            if can_water:
                row.append(InlineKeyboardButton(text=f"💧 Полить #{i+1}", callback_data=f"farm_water_{i}"))
            if can_fert:
                row.append(InlineKeyboardButton(text=f"🌿 Удобрить #{i+1}", callback_data=f"farm_fert_{i}"))
            if row:
                buttons.append(row)
            # Always show dig button for growing plots
            buttons.append([InlineKeyboardButton(
                text=f"⛏ Выкопать #{i+1}",
                callback_data=f"farm_dig_{i}"
            )])
        elif status == "ready":
            buttons.append([InlineKeyboardButton(
                text=f"🌾 Собрать {plant.get('emoji','')} #{i+1} (+{plant.get('reward',0)//100} ₽)",
                callback_data=f"farm_harvest_{i}"
            )])
        elif status == "dead":
            buttons.append([InlineKeyboardButton(
                text=f"☠️ Убрать #{i+1}",
                callback_data=f"farm_remove_{i}"
            )])
    
    # Buy plot button
    if plot_count < FARM_MAX_PLOTS:
        price = FARM_PLOT_PRICE_KOPECKS
        price_rub = price // 100
        remaining = FARM_MAX_PLOTS - plot_count
        if balance >= price:
            buttons.append([InlineKeyboardButton(
                text=f"➕ Купить грядку — {price_rub} ₽ (осталось мест: {remaining})",
                callback_data="farm_buy_plot"
            )])
        else:
            buttons.append([InlineKeyboardButton(
                text=f"➕ Грядка (нужно {price_rub} ₽, осталось мест: {remaining})",
                callback_data="farm_noop"
            )])
    
    buttons.append([InlineKeyboardButton(
        text="📖 Инструкция",
        url="https://telegra.ph/Instrukciya-Ferma-02-20"
    )])
    buttons.append([InlineKeyboardButton(text="🔙 К играм", callback_data="games_menu")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    try:
        await safe_edit_text(callback.message,text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


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

    await callback.answer()

    # During an announced storm planting is disabled to prevent the
    # replant + early-harvest loop and to keep the rule simple for players.
    if await _get_imminent_storm() is not None:
        await callback.answer(
            "🚫 Идёт шторм — посадка временно недоступна. "
            "После шторма можно будет сажать снова.",
            show_alert=True,
        )
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    plot_id = int(callback.data.split("_")[-1])
    
    buttons = []
    for key, plant in PLANT_TYPES.items():
        buttons.append([InlineKeyboardButton(
            text=f"{plant['emoji']} {plant['name']} — {plant['days']} дн. → +{plant['reward']//100} ₽",
            callback_data=f"farm_plant_{plot_id}_{key}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="game_farm")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    await safe_edit_text(callback.message,
        f"🌱 <b>Выберите растение для грядки {plot_id+1}:</b>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("farm_plant_"))
async def callback_farm_plant(callback: CallbackQuery, state: FSMContext):
    """Plant a seed"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return

    await callback.answer()

    # Server-side gate — must match the farm_choose_ guard.  A user could
    # otherwise hand-craft farm_plant_<plot>_<type> to bypass the menu hide.
    if await _get_imminent_storm() is not None:
        await callback.answer(
            "🚫 Идёт шторм — посадка временно недоступна.",
            show_alert=True,
        )
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    parts = callback.data.split("_")
    plot_id = int(parts[2])
    plant_type = parts[3]
    
    if plant_type not in PLANT_TYPES:
        await callback.answer("Неизвестный тип растения", show_alert=True)
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
        await callback.answer("Грядка недоступна", show_alert=True)
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
    
    await callback.answer()
    
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
        await callback.answer("Грядка недоступна", show_alert=True)
        return
    
    now = datetime.now(timezone.utc)
    water_used = plot.get("water_used_at")
    if water_used:
        water_time = datetime.fromisoformat(water_used)
        if (now - water_time).total_seconds() < 86400:
            await callback.answer("Вы уже поливали сегодня!", show_alert=True)
            return
    
    # Reduce ready_at by 6 hours
    ready_at = datetime.fromisoformat(plot["ready_at"])
    plot["ready_at"] = (ready_at - timedelta(hours=6)).isoformat()
    plot["water_used_at"] = now.isoformat()
    
    await database.save_farm_plots(telegram_id, farm_plots)
    await _render_farm(callback, pool, farm_plots, plot_count, balance)


@router.callback_query(F.data.startswith("farm_fert_"))
async def callback_farm_fert(callback: CallbackQuery, state: FSMContext):
    """Fertilize a plant"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    await callback.answer()
    
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
        await callback.answer("Грядка недоступна", show_alert=True)
        return
    
    now = datetime.now(timezone.utc)
    fert_used = plot.get("fertilizer_used_at")
    if fert_used:
        fert_time = datetime.fromisoformat(fert_used)
        if (now - fert_time).total_seconds() < 86400:
            await callback.answer("Вы уже удобряли сегодня!", show_alert=True)
            return
    
    # Reduce ready_at by 2 hours
    ready_at = datetime.fromisoformat(plot["ready_at"])
    plot["ready_at"] = (ready_at - timedelta(hours=2)).isoformat()
    plot["fertilizer_used_at"] = now.isoformat()
    
    await database.save_farm_plots(telegram_id, farm_plots)
    await _render_farm(callback, pool, farm_plots, plot_count, balance)


@router.callback_query(F.data.startswith("farm_harvest_"))
async def callback_farm_harvest(callback: CallbackQuery, state: FSMContext):
    """Harvest a ready plant"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    await callback.answer()
    
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
        await callback.answer("Растение не готово к сбору", show_alert=True)
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
            await callback.answer("Эта грядка уже собрана", show_alert=True)
        else:
            logger.warning(
                "FARM_HARVEST_FAILED user=%s plot=%s reason=%s",
                telegram_id, plot_id, reason,
            )
            await callback.answer("Ошибка при начислении награды", show_alert=True)
        return

    # Refresh balance
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    await _render_farm(callback, pool, farm_plots, plot_count, balance)

    await callback.answer(f"🌾 Урожай собран! +{reward_rubles:.0f} ₽", show_alert=True)


@router.callback_query(F.data.startswith("farm_remove_"))
async def callback_farm_remove(callback: CallbackQuery, state: FSMContext):
    """Remove dead plant - show confirmation"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    await callback.answer()
    
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
            text="✅ Да, убрать",
            callback_data=f"farm_remove_confirm_{plot_id}"
        )],
        [InlineKeyboardButton(
            text="❌ Нет",
            callback_data="farm_noop"
        )]
    ])
    
    await safe_edit_text(callback.message,
        "Хотите убрать погибшее растение?",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "farm_buy_plot")
async def callback_farm_buy_plot(callback: CallbackQuery, state: FSMContext):
    """Buy a new plot"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    await callback.answer()
    
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
    
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)

    if plot_count >= FARM_MAX_PLOTS:
        await callback.answer("Максимальное количество грядок достигнуто", show_alert=True)
        return

    price = FARM_PLOT_PRICE_KOPECKS
    if balance < price:
        await callback.answer("Недостаточно средств", show_alert=True)
        return

    # Deduct balance
    success = await database.decrease_balance(
        telegram_id=telegram_id,
        amount=price / 100.0,
        source="farm_buy_plot",
        description="Farm plot purchase"
    )
    
    if not success:
        await callback.answer("Ошибка при списании средств", show_alert=True)
        return
    
    # Add new empty plot
    new_plot = {
        "plot_id": plot_count,
        "status": "empty",
        "plant_type": None,
        "planted_at": None,
        "ready_at": None,
        "dead_at": None,
        "notified_ready": False,
        "notified_12h": False,
        "notified_dead": False,
        "water_used_at": None,
        "fertilizer_used_at": None
    }
    farm_plots.append(new_plot)
    plot_count += 1
    
    await database.save_farm_plots(telegram_id, farm_plots)
    await database.update_farm_plot_count(telegram_id, plot_count)
    
    # Refresh balance
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    await _render_farm(callback, pool, farm_plots, plot_count, balance)


@router.callback_query(F.data.startswith("farm_dig_") & ~F.data.startswith("farm_dig_confirm_"), StateFilter("*"))
async def callback_farm_dig(callback: CallbackQuery, state: FSMContext):
    """Show confirmation dialog for digging up a plant"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    await callback.answer()
    
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
        await callback.answer("❌ Растение недоступно для выкапывания", show_alert=True)
        return
    
    plant_type = plot.get("plant_type", "")
    plant = PLANT_TYPES.get(plant_type, {})
    plant_name = plant.get("name", "растение")
    
    # Show confirmation with inline keyboard
    confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="⛏ Да, выкопать",
                callback_data=f"farm_dig_confirm_{plot_id}"
            ),
            InlineKeyboardButton(
                text="❌ Нет",
                callback_data="game_farm"
            )
        ]
    ])
    
    await safe_edit_text(callback.message,
        f"⛏ <b>Выкопать растение?</b>\n\n"
        f"Вы хотите выкопать <b>{plant_name}</b> на грядке {plot_id+1}?\n\n"
        f"⚠️ Растение будет уничтожено без награды.\n"
        f"Грядка станет пустой и можно будет посадить новое растение.",
        reply_markup=confirm_keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("farm_dig_confirm_"), StateFilter("*"))
async def callback_farm_dig_confirm(callback: CallbackQuery, state: FSMContext):
    """Confirm and execute digging up a plant"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    await callback.answer()
    
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
        await callback.answer("❌ Грядка не найдена", show_alert=True)
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
    await callback.answer("⛏ Растение выкопано! Грядка свободна.", show_alert=True)
    await _render_farm(callback, pool, farm_plots=farm_plots, 
                       plot_count=plot_count, balance=balance)


@router.callback_query(F.data == "farm_noop")
async def callback_farm_noop(callback: CallbackQuery):
    """No-op handler for disabled buttons"""
    await callback.answer()


# ════════════════════════════════════════════════════════════════════════
# FARM STORM — shield purchase + early harvest
# ════════════════════════════════════════════════════════════════════════

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


@router.callback_query(F.data.startswith("farm_shield:"))
async def callback_farm_shield(callback: CallbackQuery):
    """🛡 Накрыть — pay via balance if enough, else show Lava/SBP screen."""
    if not await ensure_db_ready_callback(callback):
        return
    await callback.answer()
    telegram_id = callback.from_user.id

    storm = await _get_imminent_storm()
    if storm is None:
        await callback.answer("Шторм уже прошёл или ещё не объявлен.", show_alert=True)
        return

    plot_id = _parse_plot_id(callback.data, "farm_shield")
    if plot_id < 0:
        return
    farm_plots, plot_count, balance, plot = await _find_growing_plot(telegram_id, plot_id)
    if plot is None:
        await callback.answer("Грядка больше не растёт.", show_alert=True)
        return
    if plot.get("storm_shielded"):
        await callback.answer("Грядка уже накрыта.", show_alert=True)
        return

    plant = PLANT_TYPES.get(plot.get("plant_type"), {})
    shield_cost = storm_shield_price_kopecks(int(plant.get("reward", 0)))
    shield_cost_rub = shield_cost // 100

    if balance >= shield_cost:
        ok, reason = await database.apply_storm_shield_atomic(
            telegram_id, plot_id, shield_cost, deduct_balance=True,
        )
        if ok:
            await callback.answer(f"🛡 Грядка накрыта (−{shield_cost_rub} ₽)", show_alert=True)
        else:
            await callback.answer(f"Не удалось накрыть: {reason}", show_alert=True)
        pool = await database.get_pool()
        await _render_farm(callback, pool)
        return

    # Balance not enough → payment screen
    need_rub = (shield_cost - balance) / 100.0
    text = (
        f"🛡 <b>Накрытие грядки {plot_id + 1}</b>\n\n"
        f"Растение: {plant.get('emoji','')} {plant.get('name','')}\n"
        f"Цена плёнки: <b>{shield_cost_rub} ₽</b>\n"
        f"На балансе: {balance / 100:.2f} ₽ (не хватает {need_rub:.2f} ₽)\n\n"
        f"Выберите способ оплаты:"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Картой", callback_data=f"farm_shield_lava:{plot_id}")],
        [InlineKeyboardButton(text="📲 СБП", callback_data=f"farm_shield_sbp:{plot_id}")],
        [InlineKeyboardButton(text="🔙 На ферму", callback_data="game_farm")],
    ])
    try:
        await safe_edit_text(callback.message,text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


@router.callback_query(F.data.startswith("farm_shield_lava:"))
async def callback_farm_shield_lava(callback: CallbackQuery):
    """Pay shield via Lava (card)."""
    if not await ensure_db_ready_callback(callback):
        return
    telegram_id = callback.from_user.id

    plot_id = _parse_plot_id(callback.data, "farm_shield_lava")
    if plot_id < 0:
        return
    _, _, _, plot = await _find_growing_plot(telegram_id, plot_id)
    if plot is None:
        await callback.answer("Грядка больше не растёт.", show_alert=True)
        return
    plant = PLANT_TYPES.get(plot.get("plant_type"), {})
    shield_cost = storm_shield_price_kopecks(int(plant.get("reward", 0)))

    import lava_service
    if not lava_service.is_enabled():
        await callback.answer("Оплата картой временно недоступна.", show_alert=True)
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
            comment=f"Atlas Secure — Накрытие грядки {plot_id + 1}",
        )
        invoice_id = invoice["invoice_id"]
        payment_url = invoice["payment_url"]
        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(invoice_id))
        except Exception as e:
            logger.error("Failed to save Lava invoice_id: %s", e)

        text = (
            f"💳 <b>Оплата накрытия грядки</b>\n\n"
            f"Сумма: {shield_cost // 100} ₽\n\n"
            f"После оплаты грядка накроется автоматически."
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Перейти к оплате", url=payment_url)],
            [InlineKeyboardButton(text="🔙 На ферму", callback_data="game_farm")],
        ])
        await safe_edit_text(callback.message,text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()
    except Exception as e:
        logger.exception("FARM_SHIELD_LAVA_ERROR user=%s plot=%s: %s", telegram_id, plot_id, e)
        await callback.answer("Ошибка создания платежа.", show_alert=True)


@router.callback_query(F.data.startswith("farm_shield_sbp:"))
async def callback_farm_shield_sbp(callback: CallbackQuery):
    """Pay shield via Платега (SBP, +11%)."""
    if not await ensure_db_ready_callback(callback):
        return
    telegram_id = callback.from_user.id

    plot_id = _parse_plot_id(callback.data, "farm_shield_sbp")
    if plot_id < 0:
        return
    _, _, _, plot = await _find_growing_plot(telegram_id, plot_id)
    if plot is None:
        await callback.answer("Грядка больше не растёт.", show_alert=True)
        return
    plant = PLANT_TYPES.get(plot.get("plant_type"), {})
    shield_cost = storm_shield_price_kopecks(int(plant.get("reward", 0)))

    import platega_service
    if not platega_service.is_enabled():
        await callback.answer("СБП временно недоступен.", show_alert=True)
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
            description=f"Atlas Secure — Накрытие грядки {plot_id + 1}",
            purchase_id=purchase_id,
        )
        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(tx["transaction_id"]))
        except Exception as e:
            logger.error("Failed to save SBP tx_id: %s", e)

        text = (
            f"📲 <b>СБП — накрытие грядки</b>\n\n"
            f"Сумма с учётом наценки: {sbp_kopecks / 100:.2f} ₽\n\n"
            f"После оплаты грядка накроется автоматически."
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📲 Оплатить через СБП", url=tx["redirect_url"])],
            [InlineKeyboardButton(text="🔙 На ферму", callback_data="game_farm")],
        ])
        await safe_edit_text(callback.message,text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()
    except Exception as e:
        logger.exception("FARM_SHIELD_SBP_ERROR user=%s plot=%s: %s", telegram_id, plot_id, e)
        await callback.answer("Ошибка создания платежа.", show_alert=True)


@router.callback_query(F.data.startswith("farm_early:"))
async def callback_farm_early_harvest(callback: CallbackQuery):
    """🚜 Собрать незрелым — credits 50% of plant reward, frees the plot."""
    if not await ensure_db_ready_callback(callback):
        return
    await callback.answer()
    telegram_id = callback.from_user.id

    storm = await _get_imminent_storm()
    if storm is None:
        await callback.answer("Ранний сбор доступен только во время шторма.", show_alert=True)
        return

    plot_id = _parse_plot_id(callback.data, "farm_early")
    if plot_id < 0:
        return
    farm_plots, plot_count, balance, plot = await _find_growing_plot(telegram_id, plot_id)
    if plot is None:
        await callback.answer("Грядка больше не растёт.", show_alert=True)
        return

    plant = PLANT_TYPES.get(plot.get("plant_type"), {})
    half_reward_kopecks = int(plant.get("reward", 0)) // 2
    if half_reward_kopecks <= 0:
        await callback.answer("Ранний сбор недоступен для этого растения.", show_alert=True)
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
            await callback.answer("Эта грядка уже собрана", show_alert=True)
        else:
            logger.warning(
                "FARM_EARLY_HARVEST_FAILED user=%s plot=%s reason=%s",
                telegram_id, plot_id, reason,
            )
            await callback.answer("Не удалось зачислить награду.", show_alert=True)
        return

    await callback.answer(
        f"🚜 Собрано {plant.get('emoji','')} незрелым: +{half_reward_kopecks // 100} ₽",
        show_alert=True,
    )
    pool = await database.get_pool()
    await _render_farm(callback, pool)
