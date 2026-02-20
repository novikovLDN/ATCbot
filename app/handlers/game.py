"""
Games handlers — Bowling, Dice, Bomber.
Webhook-safe: callback.answer() before long ops; no DB connection held during dice animation.
"""
import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Set

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter

import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.keyboards import get_back_keyboard
from app.handlers.common.states import BomberState

router = Router()
logger = logging.getLogger(__name__)


def get_games_menu_keyboard(language: str) -> InlineKeyboardMarkup:
    """Games menu keyboard"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🎳 Боулинг",
            callback_data="game_bowling"
        )],
        [InlineKeyboardButton(
            text="🎲 Кубики",
            callback_data="game_dice"
        )],
        [InlineKeyboardButton(
            text="💣 Бомбер",
            callback_data="game_bomber"
        )],
        [InlineKeyboardButton(
            text="🌾 Ферма",
            callback_data="game_farm"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main"
        )],
    ])


def get_games_back_keyboard(language: str) -> InlineKeyboardMarkup:
    """Back to games menu keyboard"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🔙 К играм",
            callback_data="games_menu"
        )],
    ])


@router.callback_query(F.data == "games_menu")
async def callback_games_menu(callback: CallbackQuery):
    """Games menu screen"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    await callback.answer()
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    text = (
        "🎮 Добро пожаловать в Игровой зал!\n"
        "Здесь вы можете отвлечься и попытать удачу — а заодно выиграть дополнительные дни подписки.\n\n"
        "🎳 Боулинг — сбей кегли и получи бонусные дни\n"
        "🎲 Кубики — брось кубик и получи столько дней подписки, сколько выпало\n"
        "💣 Бомбер — стратегическая игра на выживание\n\n"
        "Выбирай игру и испытай удачу! 🍀"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=get_games_menu_keyboard(language),
    )


@router.callback_query(F.data == "game_bowling")
async def callback_game_bowling(callback: CallbackQuery, bot: Bot = None):
    """Bowling game: cooldown → subscription check → consume cooldown → dice → result."""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return

    await callback.answer()

    bot = bot or callback.bot
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    chat_id = callback.message.chat.id

    try:
        pool = await database.get_pool()
        if not pool:
            await callback.message.edit_text(
                i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
                reply_markup=get_back_keyboard(language),
            )
            logger.info("GAME_BOWL [user=%s] pool unavailable", telegram_id)
            return

        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO users (telegram_id, language) VALUES ($1, $2) ON CONFLICT (telegram_id) DO NOTHING",
                telegram_id,
                language,
            )
            user_row = await conn.fetchrow(
                "SELECT game_last_played FROM users WHERE telegram_id = $1",
                telegram_id,
            )
            game_last_played_raw = user_row.get("game_last_played") if user_row else None
            game_last_played = (
                database._from_db_utc(game_last_played_raw) if game_last_played_raw else None
            )
            now = datetime.now(timezone.utc)
            cooldown = timedelta(days=7)

            if game_last_played:
                time_since = now - game_last_played
                if time_since < cooldown:
                    remaining = cooldown - time_since
                    days = remaining.days
                    hours = remaining.seconds // 3600
                    text = (
                        "Боулинг-клуб закрыт 🎳\n"
                        f"Следующая игра доступна через: {days}д {hours}ч"
                    )
                    await callback.message.edit_text(
                        text,
                        reply_markup=get_games_back_keyboard(language),
                    )
                    logger.info(
                        "GAME_BOWL [user=%s] cooldown days=%s hours=%s",
                        telegram_id, days, hours,
                    )
                    return

            subscription = await database.get_subscription(telegram_id)
            if not subscription:
                paywall_text = (
                    "🎳 Боулинг-клуб только для подписчиков!\n\n"
                    "Приобретите подписку, чтобы играть."
                )
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text=i18n_get_text(language, "main.buy"),
                        callback_data="menu_buy_vpn",
                    )],
                    [InlineKeyboardButton(
                        text=i18n_get_text(language, "common.back"),
                        callback_data="menu_main",
                    )],
                ])
                await callback.message.edit_text(paywall_text, reply_markup=keyboard)
                logger.info("GAME_BOWL [user=%s] no_subscription paywall", telegram_id)
                return

            await conn.execute(
                "UPDATE users SET game_last_played = $1 WHERE telegram_id = $2",
                database._to_db_utc(now),
                telegram_id,
            )

        dice_message = await bot.send_dice(chat_id=chat_id, emoji="🎳")
        await asyncio.sleep(4)
        dice_value = dice_message.dice.value

        if dice_value == 6:
            try:
                result = await database.grant_access(
                    telegram_id=telegram_id,
                    duration=timedelta(days=7),
                    source="game_strike",
                )
                end_dt = result.get("subscription_end")
                if end_dt and hasattr(end_dt, "strftime"):
                    end_str = end_dt.strftime("%d.%m.%Y")
                else:
                    end_str = "—"
                text = (
                    "🎳 <b>Страйк!</b> Все кегли сбиты!\n\n"
                    "🎉 Поздравляем! Вы выиграли +7 дней подписки.\n\n"
                    f"Доступ до: {end_str}"
                )
                logger.info(
                    "GAME_BOWL [user=%s] strike=True dice_value=6 grant_ok expires=%s",
                    telegram_id, end_str,
                )
            except Exception as e:
                logger.error("GAME_BOWL [user=%s] strike=True grant_error=%s", telegram_id, e)
                text = (
                    "🎳 <b>Страйк!</b> Все кегли сбиты!\n\n"
                    "🎉 Поздравляем! Вы выиграли +7 дней подписки.\n\n"
                    "⚠️ Ошибка при начислении. Обратитесь в поддержку."
                )
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=get_games_back_keyboard(language),
                parse_mode="HTML",
            )
        else:
            text = (
                f"🎳 Вы сбили {dice_value} кеглей из 6.\n\n"
                "Увы, не страйк 😔 Попробуйте снова через 7 дней!"
            )
            logger.info("GAME_BOWL [user=%s] strike=False dice_value=%s", telegram_id, dice_value)
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=get_games_back_keyboard(language),
                parse_mode="HTML",
            )

    except Exception as e:
        logger.exception("GAME_BOWL [user=%s] error=%s", telegram_id, e)
        await callback.message.edit_text(
            i18n_get_text(language, "errors.generic", "Произошла ошибка. Попробуйте позже."),
            reply_markup=get_games_back_keyboard(language),
        )


@router.callback_query(F.data == "game_dice")
async def callback_game_dice(callback: CallbackQuery, bot: Bot = None):
    """Dice game: cooldown → subscription check → consume cooldown → dice → grant days."""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return

    await callback.answer()

    bot = bot or callback.bot
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    chat_id = callback.message.chat.id

    try:
        pool = await database.get_pool()
        if not pool:
            await callback.message.edit_text(
                i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
                reply_markup=get_games_back_keyboard(language),
            )
            logger.info("GAME_DICE [user=%s] pool unavailable", telegram_id)
            return

        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO users (telegram_id, language) VALUES ($1, $2) ON CONFLICT (telegram_id) DO NOTHING",
                telegram_id,
                language,
            )
            user_row = await conn.fetchrow(
                "SELECT dice_last_played FROM users WHERE telegram_id = $1",
                telegram_id,
            )
            dice_last_played_raw = user_row.get("dice_last_played") if user_row else None
            dice_last_played = (
                database._from_db_utc(dice_last_played_raw) if dice_last_played_raw else None
            )
            now = datetime.now(timezone.utc)
            cooldown = timedelta(days=7)

            if dice_last_played:
                time_since = now - dice_last_played
                if time_since < cooldown:
                    remaining = cooldown - time_since
                    days = remaining.days
                    hours = remaining.seconds // 3600
                    text = (
                        "⏳ Вы уже бросали кубик!\n"
                        f"Следующий бросок доступен через: {days} дней {hours} часов"
                    )
                    await callback.message.edit_text(
                        text,
                        reply_markup=get_games_back_keyboard(language),
                    )
                    logger.info(
                        "GAME_DICE [user=%s] cooldown days=%s hours=%s",
                        telegram_id, days, hours,
                    )
                    return

            subscription = await database.get_subscription(telegram_id)
            if not subscription:
                paywall_text = (
                    "🎲 Игра в кубики только для подписчиков!\n\n"
                    "Приобретите подписку, чтобы играть."
                )
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text=i18n_get_text(language, "main.buy"),
                        callback_data="menu_buy_vpn",
                    )],
                    [InlineKeyboardButton(
                        text="🔙 К играм",
                        callback_data="games_menu",
                    )],
                ])
                await callback.message.edit_text(paywall_text, reply_markup=keyboard)
                logger.info("GAME_DICE [user=%s] no_subscription paywall", telegram_id)
                return

            await conn.execute(
                "UPDATE users SET dice_last_played = $1 WHERE telegram_id = $2",
                database._to_db_utc(now),
                telegram_id,
            )

        dice_message = await bot.send_dice(chat_id=chat_id, emoji="🎲")
        await asyncio.sleep(2)
        dice_value = dice_message.dice.value

        # Grant days equal to dice value (1-6)
        try:
            result = await database.grant_access(
                telegram_id=telegram_id,
                duration=timedelta(days=dice_value),
                source="game_dice",
            )
            end_dt = result.get("subscription_end")
            if end_dt and hasattr(end_dt, "strftime"):
                end_str = end_dt.strftime("%d.%m.%Y")
            else:
                end_str = "—"
            text = (
                f"🎲 Выпало: {dice_value}!\n\n"
                f"🎉 Вам начислено {dice_value} дней подписки!\n\n"
                f"Ваша подписка действует до: {end_str}"
            )
            logger.info(
                "GAME_DICE [user=%s] dice_value=%s grant_ok expires=%s",
                telegram_id, dice_value, end_str,
            )
        except Exception as e:
            logger.error("GAME_DICE [user=%s] dice_value=%s grant_error=%s", telegram_id, dice_value, e)
            text = (
                f"🎲 Выпало: {dice_value}!\n\n"
                f"🎉 Вам начислено {dice_value} дней подписки!\n\n"
                "⚠️ Ошибка при начислении. Обратитесь в поддержку."
            )
        
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=get_games_back_keyboard(language),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.exception("GAME_DICE [user=%s] error=%s", telegram_id, e)
        await callback.message.edit_text(
            i18n_get_text(language, "errors.generic", "Произошла ошибка. Попробуйте позже."),
            reply_markup=get_games_back_keyboard(language),
        )


def create_bomber_grid_keyboard(mines: Set[int], player_bombs: Set[int], game_over: bool = False) -> InlineKeyboardMarkup:
    """Create 5x5 grid keyboard for bomber game"""
    buttons = []
    for row in range(5):
        row_buttons = []
        for col in range(5):
            cell_idx = row * 5 + col
            if game_over:
                if cell_idx in mines:
                    emoji = "💥"
                elif cell_idx in player_bombs:
                    emoji = "💣"
                else:
                    emoji = "⬜"
            else:
                if cell_idx in player_bombs:
                    emoji = "💣"
                else:
                    emoji = "⬜"
            row_buttons.append(InlineKeyboardButton(
                text=emoji,
                callback_data=f"bomber_cell:{cell_idx}"
            ))
        buttons.append(row_buttons)
    
    if not game_over:
        buttons.append([InlineKeyboardButton(
            text="🚩 Завершить",
            callback_data="bomber_exit"
        )])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "game_bomber")
async def callback_game_bomber(callback: CallbackQuery, state: FSMContext):
    """Start Bomber game - initialize grid with 3 random mines"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    await callback.answer()
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # Initialize game: 3 random mines on 5x5 grid (25 cells, indices 0-24)
    mines = set(random.sample(range(25), 3))
    player_bombs: Set[int] = set()
    
    await state.set_state(BomberState.playing)
    await state.update_data(
        mines=list(mines),
        player_bombs=list(player_bombs),
    )
    
    text = (
        "💣 Бомбер\n\n"
        "Правила:\n"
        "• Размещайте бомбы на поле, избегая мин бота\n"
        "• Если наступите на свою бомбу — взрыв! 💥\n"
        "• Если наступите на мину бота — взрыв! 💥\n"
        "• Нажмите 'Завершить' чтобы безопасно выйти\n\n"
        "Удачи! 🍀"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=create_bomber_grid_keyboard(mines, player_bombs),
    )


@router.callback_query(F.data.startswith("bomber_cell:"), BomberState.playing)
async def callback_bomber_cell(callback: CallbackQuery, state: FSMContext):
    """Handle cell click in Bomber game"""
    await callback.answer()
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    try:
        cell_idx = int(callback.data.split(":")[1])
        
        data = await state.get_data()
        mines = set(data.get("mines", []))
        player_bombs = set(data.get("player_bombs", []))
        
        # Check if player clicked their own bomb
        if cell_idx in player_bombs:
            # Self-destruct!
            await state.clear()
            text = (
                "🧨 БУМ! Вы подорвались на своей бомбе!\n\n"
                "Игра окончена. Попробуйте ещё раз!"
            )
            await callback.message.edit_text(
                text,
                reply_markup=get_games_back_keyboard(language),
            )
            logger.info("GAME_BOMBER [user=%s] self_destruct cell=%s", telegram_id, cell_idx)
            return
        
        # Check if player clicked a bot mine
        if cell_idx in mines:
            # Game over!
            await state.clear()
            text = (
                "💥 БУМ! Вы подорвались на мине бота!\n\n"
                "Игра окончена. Попробуйте ещё раз!"
            )
            await callback.message.edit_text(
                text,
                reply_markup=create_bomber_grid_keyboard(mines, player_bombs, game_over=True),
            )
            await asyncio.sleep(2)
            await callback.message.edit_text(
                text,
                reply_markup=get_games_back_keyboard(language),
            )
            logger.info("GAME_BOMBER [user=%s] mine_exploded cell=%s", telegram_id, cell_idx)
            return
        
        # Safe cell - place bomb
        player_bombs.add(cell_idx)
        await state.update_data(player_bombs=list(player_bombs))
        
        # Update grid
        await callback.message.edit_reply_markup(
            reply_markup=create_bomber_grid_keyboard(mines, player_bombs),
        )
        
    except Exception as e:
        logger.exception("GAME_BOMBER [user=%s] error=%s", telegram_id, e)
        await state.clear()
        await callback.message.edit_text(
            i18n_get_text(language, "errors.generic", "Произошла ошибка. Попробуйте позже."),
            reply_markup=get_games_back_keyboard(language),
        )


@router.callback_query(F.data == "bomber_exit", BomberState.playing)
async def callback_bomber_exit(callback: CallbackQuery, state: FSMContext):
    """Safe exit from Bomber game"""
    await callback.answer()
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    try:
        data = await state.get_data()
        player_bombs = set(data.get("player_bombs", []))
        bomb_count = len(player_bombs)
        
        await state.clear()
        
        text = (
            f"😮‍💨 Вы вышли из игры целым!\n\n"
            f"Выжило бомб: {bomb_count}"
        )
        
        await callback.message.edit_text(
            text,
            reply_markup=get_games_back_keyboard(language),
        )
        
        logger.info("GAME_BOMBER [user=%s] safe_exit bombs=%s", telegram_id, bomb_count)
        
    except Exception as e:
        logger.exception("GAME_BOMBER_EXIT [user=%s] error=%s", telegram_id, e)
        await state.clear()
        await callback.message.edit_text(
            i18n_get_text(language, "errors.generic", "Произошла ошибка. Попробуйте позже."),
            reply_markup=get_games_back_keyboard(language),
        )


# ====================================================================================
# FARM GAME HANDLERS
# ====================================================================================

def format_time_remaining(seconds: int) -> str:
    """Format seconds to 'Xч Yм' format"""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}ч {minutes}м"
    return f"{minutes}м"


def sync_farm_plot_statuses(farm_plots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Sync plot statuses based on planted_at + 144 hours (6 days)
    
    Returns:
        Updated farm_plots list with synced statuses
    """
    now = datetime.now(timezone.utc)
    growth_time = timedelta(hours=144)  # 6 days
    
    updated_plots = []
    for plot in farm_plots:
        plot = plot.copy()
        status = plot.get("status", "empty")
        planted_at_str = plot.get("planted_at")
        
        if status == "growing" and planted_at_str:
            try:
                if isinstance(planted_at_str, str):
                    planted_at = datetime.fromisoformat(planted_at_str.replace("Z", "+00:00"))
                else:
                    planted_at = planted_at_str
                
                if planted_at.tzinfo is None:
                    planted_at = planted_at.replace(tzinfo=timezone.utc)
                
                ready_time = planted_at + growth_time
                if now >= ready_time:
                    plot["status"] = "ready"
                plot["planted_at"] = planted_at.isoformat()
            except Exception as e:
                logger.error(f"Error syncing plot status: {e}")
                plot["status"] = "empty"
                plot["planted_at"] = None
        
        updated_plots.append(plot)
    
    return updated_plots


def create_farm_keyboard(farm_plots: List[Dict[str, Any]], farm_plot_count: int, bonus_balance: float, can_buy_plot: bool, upgrade_price: float) -> InlineKeyboardMarkup:
    """Create inline keyboard for farm screen"""
    buttons = []
    now = datetime.now(timezone.utc)
    growth_time = timedelta(hours=144)  # 6 days
    bad_weather_warning_threshold = timedelta(days=3)  # Show warning after 3 days
    
    # Plot buttons
    for plot_idx in range(farm_plot_count):
        plot = next((p for p in farm_plots if p.get("plot_id") == plot_idx), None)
        if not plot:
            plot = {"plot_id": plot_idx, "status": "empty", "planted_at": None, "weather": None}
        
        status = plot.get("status", "empty")
        weather = plot.get("weather")
        planted_at_str = plot.get("planted_at")
        
        if status == "empty":
            buttons.append([InlineKeyboardButton(
                text=f"🌱 Посадить #{plot_idx + 1}",
                callback_data=f"farm_plant_{plot_idx}"
            )])
        elif status == "growing":
            # Check if bad weather warning should be shown
            show_bad_weather_warning = False
            if weather == "bad" and planted_at_str:
                try:
                    if isinstance(planted_at_str, str):
                        planted_at = datetime.fromisoformat(planted_at_str.replace("Z", "+00:00"))
                    else:
                        planted_at = planted_at_str
                    
                    if planted_at.tzinfo is None:
                        planted_at = planted_at.replace(tzinfo=timezone.utc)
                    
                    time_since_planted = now - planted_at
                    if time_since_planted >= bad_weather_warning_threshold:
                        show_bad_weather_warning = True
                except Exception:
                    pass
            
            if show_bad_weather_warning:
                buttons.append([InlineKeyboardButton(
                    text=f"🌧 Пересадить #{plot_idx + 1}",
                    callback_data=f"farm_replant_{plot_idx}"
                )])
            else:
                buttons.append([InlineKeyboardButton(
                    text=f"⏳ Растёт #{plot_idx + 1}",
                    callback_data="farm_noop"
                )])
        elif status == "ready":
            buttons.append([InlineKeyboardButton(
                text=f"🌻 Собрать #{plot_idx + 1}",
                callback_data=f"farm_harvest_{plot_idx}"
            )])
    
    # Buy plot button
    if can_buy_plot and farm_plot_count < 5:
        buttons.append([InlineKeyboardButton(
            text=f"➕ Купить грядку — {int(upgrade_price)} ₽",
            callback_data="farm_buy_plot"
        )])
    
    # Back button
    buttons.append([InlineKeyboardButton(
        text="🔙 К играм",
        callback_data="games_menu"
    )])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_upgrade_price(current_count: int) -> float:
    """Get upgrade price for next plot"""
    prices = {1: 50.0, 2: 100.0, 3: 200.0, 4: 400.0}
    return prices.get(current_count, 0.0)


async def _render_farm(callback: CallbackQuery, pool) -> None:
    """Builds farm text + keyboard and edits the message."""
    telegram_id = callback.from_user.id
    
    # 1. Load data
    farm_data = await database.get_farm_data(telegram_id)
    farm_plots = farm_data.get("farm_plots", [])
    plot_count = farm_data.get("farm_plot_count", 1)
    balance_rubles = await database.get_user_balance(telegram_id)
    balance_kopecks = int(balance_rubles * 100)  # Convert to kopecks for comparison
    
    async with pool.acquire() as conn:
        last_good = await database.get_farm_last_good_harvest(telegram_id, conn=conn)
    
    # Initialize plots if empty
    if not farm_plots:
        farm_plots = [{"plot_id": 0, "status": "empty", "planted_at": None, "weather": None}]
        await database.save_farm_plots(telegram_id, farm_plots)
    
    # Ensure we have correct number of plots
    while len(farm_plots) < plot_count:
        farm_plots.append({
            "plot_id": len(farm_plots),
            "status": "empty",
            "planted_at": None,
            "weather": None
        })
    
    # 2. Sync plot statuses (growing → ready if 6 days passed)
    now = datetime.now(timezone.utc)
    changed = False
    for plot in farm_plots:
        if plot["status"] == "growing" and plot.get("planted_at"):
            try:
                planted = datetime.fromisoformat(plot["planted_at"].replace("Z", "+00:00"))
                if planted.tzinfo is None:
                    planted = planted.replace(tzinfo=timezone.utc)
                if (now - planted).total_seconds() >= 6 * 24 * 3600:
                    plot["status"] = "ready"
                    changed = True
            except Exception as e:
                logger.error(f"Error syncing plot status: {e}")
    if changed:
        await database.save_farm_plots(telegram_id, farm_plots)
    
    # 3. Build text
    lines = ["🌾 <b>Ваша ферма</b>\n"]
    for i, plot in enumerate(farm_plots):
        status = plot["status"]
        weather = plot.get("weather", "good")
        planted_at = plot.get("planted_at")
        
        if status == "empty":
            lines.append(f"Грядка {i+1}: ⬜ Пусто")
        elif status == "growing":
            planted = datetime.fromisoformat(planted_at.replace("Z", "+00:00"))
            if planted.tzinfo is None:
                planted = planted.replace(tzinfo=timezone.utc)
            elapsed = (now - planted).total_seconds()
            days_elapsed = elapsed / 86400
            remaining_secs = max(0, 6*24*3600 - elapsed)
            remaining_days = int(remaining_secs // 86400)
            remaining_hours = int((remaining_secs % 86400) // 3600)
            
            if weather == "bad" and days_elapsed >= 3:
                lines.append(f"Грядка {i+1}: ⛈ Плохая погода — урожай гибнет")
            else:
                lines.append(f"Грядка {i+1}: 🌱 Растёт — осталось {remaining_days}д {remaining_hours}ч")
        elif status == "ready":
            weather = plot.get("weather", "good")
            if weather == "good":
                lines.append(f"Грядка {i+1}: 🌻 Урожай готов!")
            else:
                lines.append(f"Грядка {i+1}: 🥀 Плохой урожай (можно собрать)")
    
    # Upgrade price table
    upgrade_prices = {1: 50, 2: 100, 3: 200, 4: 400}
    next_price = upgrade_prices.get(plot_count)
    
    lines.append(f"\n💰 Баланс: {balance_rubles:.2f} ₽")
    text = "\n".join(lines)
    
    # 4. Build keyboard
    buttons = []
    for plot in farm_plots:
        i = plot["plot_id"]
        status = plot["status"]
        weather = plot.get("weather", "good")
        planted_at = plot.get("planted_at")
        
        if status == "empty":
            buttons.append([InlineKeyboardButton(
                text=f"🌱 Посадить #{i+1}",
                callback_data=f"farm_plant_{i}"
            )])
        elif status == "growing":
            planted = datetime.fromisoformat(planted_at.replace("Z", "+00:00"))
            if planted.tzinfo is None:
                planted = planted.replace(tzinfo=timezone.utc)
            elapsed_days = (now - planted).total_seconds() / 86400
            if weather == "bad" and elapsed_days >= 3:
                buttons.append([InlineKeyboardButton(
                    text=f"🔄 Пересадить #{i+1}",
                    callback_data=f"farm_replant_{i}"
                )])
            else:
                buttons.append([InlineKeyboardButton(
                    text=f"⏳ Растёт #{i+1}",
                    callback_data="farm_noop"
                )])
        elif status == "ready":
            buttons.append([InlineKeyboardButton(
                text=f"🌻 Собрать #{i+1}",
                callback_data=f"farm_harvest_{i}"
            )])
    
    if next_price and plot_count < 5:
        can_afford = balance_kopecks >= next_price * 100
        btn_text = f"➕ Купить грядку — {next_price} ₽" if can_afford else f"➕ Грядка {next_price} ₽ (недостаточно средств)"
        buttons.append([InlineKeyboardButton(
            text=btn_text,
            callback_data="farm_buy_plot" if can_afford else "farm_noop"
        )])
    
    buttons.append([InlineKeyboardButton(text="🔙 К играм", callback_data="games_menu")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


@router.callback_query(F.data == "game_farm", StateFilter("*"))
async def callback_game_farm(callback: CallbackQuery, state: FSMContext):
    telegram_id = callback.from_user.id
    pool = await database.get_pool()
    if not pool:
        await callback.answer("❌ Ошибка базы данных", show_alert=True)
        return
    
    # Check subscription
    subscription = await database.get_subscription(telegram_id)
    if not subscription:
        await callback.answer("❌ Игра доступна только с активной подпиской", show_alert=True)
        return
    
    await callback.answer()
    await _render_farm(callback, pool)


@router.callback_query(F.data.startswith("farm_plant_"), StateFilter("*"))
async def callback_farm_plant(callback: CallbackQuery, state: FSMContext):
    telegram_id = callback.from_user.id
    plot_id = int(callback.data.split("_")[-1])
    pool = await database.get_pool()
    if not pool:
        await callback.answer("❌ Ошибка базы данных", show_alert=True)
        return
    
    now = datetime.now(timezone.utc)
    
    farm_data = await database.get_farm_data(telegram_id)
    farm_plots = farm_data.get("farm_plots", [])
    async with pool.acquire() as conn:
        last_good = await database.get_farm_last_good_harvest(telegram_id, conn=conn)
    
    # Find the plot - ensure plots list has correct structure
    if not farm_plots:
        farm_plots = [{"plot_id": 0, "status": "empty", "planted_at": None, "weather": None}]
    
    plot = next((p for p in farm_plots if p.get("plot_id") == plot_id), None)
    if not plot:
        # Create new plot if doesn't exist
        plot = {"plot_id": plot_id, "status": "empty", "planted_at": None, "weather": None}
        farm_plots.append(plot)
    
    if plot["status"] != "empty":
        await callback.answer("❌ Грядка недоступна", show_alert=True)
        return
    
    # Determine weather
    force_good = (last_good is None) or ((now - last_good).days >= 30)
    if force_good:
        weather = "good"
    else:
        weather = "good" if random.random() < 0.7 else "bad"
    
    # Plant
    plot["status"] = "growing"
    plot["planted_at"] = now.isoformat()
    plot["weather"] = weather
    
    # Update plot in list
    for i, p in enumerate(farm_plots):
        if p.get("plot_id") == plot_id:
            farm_plots[i] = plot
            break
    else:
        farm_plots.append(plot)
    
    await database.save_farm_plots(telegram_id, farm_plots)
    await callback.answer("🌱 Семя посажено!")
    await _render_farm(callback, pool)
    logger.info(f"GAME_FARM [user={telegram_id}] planted plot={plot_id} weather={weather}")


@router.callback_query(F.data.startswith("farm_harvest_"), StateFilter("*"))
async def callback_farm_harvest(callback: CallbackQuery, state: FSMContext):
    telegram_id = callback.from_user.id
    plot_id = int(callback.data.split("_")[-1])
    pool = await database.get_pool()
    if not pool:
        await callback.answer("❌ Ошибка базы данных", show_alert=True)
        return
    
    now = datetime.now(timezone.utc)
    
    farm_data = await database.get_farm_data(telegram_id)
    farm_plots = farm_data.get("farm_plots", [])
    
    plot = next((p for p in farm_plots if p.get("plot_id") == plot_id), None)
    if not plot or plot.get("status") != "ready":
        await callback.answer("❌ Урожай ещё не готов", show_alert=True)
        return
    
    weather = plot.get("weather", "good")
    plot["status"] = "empty"
    plot["planted_at"] = None
    plot["weather"] = None
    
    await database.save_farm_plots(telegram_id, farm_plots)
    
    async with pool.acquire() as conn:
        if weather == "good":
            await database.increase_balance(telegram_id, 10.0, source="farm_harvest", description="Farm harvest reward", conn=conn)
            await database.update_farm_last_good_harvest(telegram_id, dt=now, conn=conn)
            await callback.answer("🌻 Отличный урожай! +10 ₽ зачислено!", show_alert=True)
            logger.info(f"GAME_FARM [user={telegram_id}] harvested plot={plot_id} weather=good +10 RUB")
        else:
            await callback.answer("🌧 Увы, урожай погиб от плохой погоды 😢", show_alert=True)
            logger.info(f"GAME_FARM [user={telegram_id}] harvested plot={plot_id} weather=bad no reward")
    
    await _render_farm(callback, pool)


@router.callback_query(F.data == "farm_buy_plot", StateFilter("*"))
async def callback_farm_buy_plot(callback: CallbackQuery, state: FSMContext):
    telegram_id = callback.from_user.id
    pool = await database.get_pool()
    if not pool:
        await callback.answer("❌ Ошибка базы данных", show_alert=True)
        return
    
    farm_data = await database.get_farm_data(telegram_id)
    farm_plots = farm_data.get("farm_plots", [])
    plot_count = farm_data.get("farm_plot_count", 1)
    balance_rubles = await database.get_user_balance(telegram_id)
    balance_kopecks = int(balance_rubles * 100)
    upgrade_prices = {1: 50, 2: 100, 3: 200, 4: 400}
    price = upgrade_prices.get(plot_count)
    
    if not price or plot_count >= 5:
        await callback.answer("❌ Максимальное количество грядок достигнуто", show_alert=True)
        return
    
    if balance_kopecks < price * 100:
        await callback.answer(f"❌ Недостаточно средств. Нужно {price} ₽", show_alert=True)
        return
    
    async with pool.acquire() as conn:
        await database.decrease_balance(telegram_id, float(price), source="farm_upgrade", description=f"Farm plot {plot_count + 1} purchase", conn=conn)
    
    new_plot = {"plot_id": plot_count, "status": "empty", "planted_at": None, "weather": None}
    farm_plots.append(new_plot)
    await database.save_farm_plots(telegram_id, farm_plots)
    await database.update_farm_plot_count(telegram_id, plot_count + 1)
    
    await callback.answer(f"✅ Куплена новая грядка за {price} ₽!", show_alert=True)
    await _render_farm(callback, pool)


@router.callback_query(F.data == "farm_noop", StateFilter("*"))
async def callback_farm_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data.startswith("farm_replant_"), StateFilter("*"))
async def callback_farm_replant(callback: CallbackQuery, state: FSMContext):
    telegram_id = callback.from_user.id
    plot_id = int(callback.data.split("_")[-1])
    pool = await database.get_pool()
    if not pool:
        await callback.answer("❌ Ошибка базы данных", show_alert=True)
        return
    
    farm_data = await database.get_farm_data(telegram_id)
    farm_plots = farm_data.get("farm_plots", [])
    plot = next((p for p in farm_plots if p.get("plot_id") == plot_id), None)
    if not plot:
        await callback.answer("❌ Грядка не найдена", show_alert=True)
        return
    
    plot["status"] = "empty"
    plot["planted_at"] = None
    plot["weather"] = None
    
    await database.save_farm_plots(telegram_id, farm_plots)
    await callback.answer("🌧 Урожай погиб. Можно посадить снова!", show_alert=True)
    await _render_farm(callback, pool)
