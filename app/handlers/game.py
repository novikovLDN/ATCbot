"""
Games handlers — Bowling, Dice, Bomber.
Webhook-safe: callback.answer() before long ops; no DB connection held during dice animation.
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

import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.keyboards import get_back_keyboard
from app.handlers.common.states import BomberState

router = Router()
logger = logging.getLogger(__name__)

# Plant types for Farm game
PLANT_TYPES = {
    "tomato":    {"emoji": "🍅", "name": "Томаты",   "days": 3,  "reward": 500},
    "potato":    {"emoji": "🥔", "name": "Картофель","days": 5,  "reward": 1000},
    "carrot":    {"emoji": "🥕", "name": "Морковь",  "days": 7,  "reward": 1000},
    "cactus":    {"emoji": "🌵", "name": "Кактус",   "days": 10, "reward": 1500},
    "apple":     {"emoji": "🍏", "name": "Яблоня",   "days": 8,  "reward": 1500},
    "lavender":  {"emoji": "💜", "name": "Лаванда",  "days": 6,  "reward": 2000},
}
# reward is in kopecks (500 = 5 RUB, 2000 = 20 RUB)


def get_games_menu_keyboard(language: str) -> InlineKeyboardMarkup:
    """Games menu keyboard"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "games.button_bowling", "🎳 Боулинг"),
            callback_data="game_bowling"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "games.button_dice", "🎲 Кубики"),
            callback_data="game_dice"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "games.button_bomber", "💣 Бомбер"),
            callback_data="game_bomber"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "games.button_farm", "🌾 Ферма"),
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
            text=i18n_get_text(language, "games.back_to_games", "🔙 К играм"),
            callback_data="games_menu"
        )],
    ])


@router.callback_query(F.data == "games_menu")
async def callback_games_menu(callback: CallbackQuery):
    """Games menu screen — subscription required (same check as bowling/dice/farm)."""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    subscription = await database.get_subscription(telegram_id)
    if not subscription:
        await callback.answer(
            i18n_get_text(language, "games.games_club_paywall"),
            show_alert=True,
        )
        return

    await callback.answer()

    text = i18n_get_text(language, "games.menu_title")

    await callback.message.edit_text(
        text,
        reply_markup=get_games_menu_keyboard(language),
        parse_mode="HTML",
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
                    text = i18n_get_text(language, "games.bowling_cooldown", "Боулинг-клуб закрыт 🎳\nСледующая игра доступна через: {days}д {hours}ч").format(days=days, hours=hours)
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
                paywall_text = i18n_get_text(language, "games.bowling_paywall", "🎳 Боулинг-клуб только для подписчиков!\n\nПриобретите подписку, чтобы играть.")
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
                text = i18n_get_text(language, "games.bowling_strike_success", "🎳 <b>Страйк!</b> Все кегли сбиты!\n\n🎉 Поздравляем! Вы выиграли +7 дней подписки.\n\nДоступ до: {date}").format(date=end_str)
                logger.info(
                    "GAME_BOWL [user=%s] strike=True dice_value=6 grant_ok expires=%s",
                    telegram_id, end_str,
                )
            except Exception as e:
                logger.error("GAME_BOWL [user=%s] strike=True grant_error=%s", telegram_id, e)
                text = i18n_get_text(language, "games.bowling_strike_error", "🎳 <b>Страйк!</b> Все кегли сбиты!\n\n🎉 Поздравляем! Вы выиграли +7 дней подписки.\n\n⚠️ Ошибка при начислении. Обратитесь в поддержку.")
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=get_games_back_keyboard(language),
                parse_mode="HTML",
            )
        else:
            text = i18n_get_text(language, "games.bowling_no_strike", "🎳 Вы сбили {value} кеглей из 6.\n\nУвы, не страйк 😔 Попробуйте снова через 7 дней!").format(value=dice_value)
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
            cooldown = timedelta(days=14)

            if dice_last_played:
                time_since = now - dice_last_played
                if time_since < cooldown:
                    remaining = cooldown - time_since
                    days = remaining.days
                    hours = remaining.seconds // 3600
                    text = i18n_get_text(language, "games.dice_cooldown", "⏳ Вы уже бросали кубик!\nСледующий бросок доступен через: {days} дней {hours} часов").format(days=days, hours=hours)
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
                paywall_text = i18n_get_text(language, "games.dice_paywall", "🎲 Игра в кубики только для подписчиков!\n\nПриобретите подписку, чтобы играть.")
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text=i18n_get_text(language, "main.buy"),
                        callback_data="menu_buy_vpn",
                    )],
                    [InlineKeyboardButton(
                        text=i18n_get_text(language, "games.back_to_games", "🔙 К играм"),
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
            text = i18n_get_text(language, "games.dice_success", "🎲 Выпало: {value}!\n\n🎉 Вам начислено {value} дней подписки!\n\nВаша подписка действует до: {date}").format(value=dice_value, date=end_str)
            logger.info(
                "GAME_DICE [user=%s] dice_value=%s grant_ok expires=%s",
                telegram_id, dice_value, end_str,
            )
        except Exception as e:
            logger.error("GAME_DICE [user=%s] dice_value=%s grant_error=%s", telegram_id, dice_value, e)
            text = i18n_get_text(language, "games.dice_error", "🎲 Выпало: {value}!\n\n🎉 Вам начислено {value} дней подписки!\n\n⚠️ Ошибка при начислении. Обратитесь в поддержку.").format(value=dice_value)
        
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


def create_bomber_grid_keyboard(mines: Set[int], player_bombs: Set[int], language: str = "ru", game_over: bool = False) -> InlineKeyboardMarkup:
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
            text=i18n_get_text(language, "games.bomber_finish", "🚩 Завершить"),
            callback_data="bomber_exit"
        )])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "game_bomber")
async def callback_game_bomber(callback: CallbackQuery, state: FSMContext):
    """Start Bomber game - initialize grid with 3 random mines"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # Subscription check (same pattern as bowling/dice)
    subscription = await database.get_subscription(telegram_id)
    if not subscription:
        paywall_text = i18n_get_text(language, "games.bomber_paywall")
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
        logger.info("GAME_BOMBER [user=%s] no_subscription paywall", telegram_id)
        return

    await callback.answer()

    # Initialize game: 3 random mines on 5x5 grid (25 cells, indices 0-24)
    mines = set(random.sample(range(25), 3))
    player_bombs: Set[int] = set()
    
    await state.set_state(BomberState.playing)
    await state.update_data(
        mines=list(mines),
        player_bombs=list(player_bombs),
    )
    
    text = i18n_get_text(language, "games.bomber_rules", "💣 Бомбер\n\nПравила:\n• Размещайте бомбы на поле, избегая мин бота\n• Если наступите на свою бомбу — взрыв! 💥\n• Если наступите на мину бота — взрыв! 💥\n• Нажмите 'Завершить' чтобы безопасно выйти\n\nУдачи! 🍀")
    
    await callback.message.edit_text(
        text,
        reply_markup=create_bomber_grid_keyboard(mines, player_bombs, language),
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
            text = i18n_get_text(language, "games.bomber_self_destruct", "🧨 БУМ! Вы подорвались на своей бомбе!\n\nИгра окончена. Попробуйте ещё раз!")
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
            text = i18n_get_text(language, "games.bomber_mine_exploded", "💥 БУМ! Вы подорвались на мине бота!\n\nИгра окончена. Попробуйте ещё раз!")
            await callback.message.edit_text(
                text,
                reply_markup=create_bomber_grid_keyboard(mines, player_bombs, language, game_over=True),
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
            reply_markup=create_bomber_grid_keyboard(mines, player_bombs, language),
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
        
        text = i18n_get_text(language, "games.bomber_safe_exit", "😮‍💨 Вы вышли из игры целым!\n\nВыжило бомб: {count}").format(count=bomb_count)
        
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
    
    # Build text (plot 0 always visible; plots 1-8 only if purchased, i.e. plot_id < plot_count)
    lines = [i18n_get_text(language, "farm.title") + "\n"]
    for plot in farm_plots:
        if plot["plot_id"] >= plot_count:
            continue
        i = plot["plot_id"]
        status = plot["status"]
        pt = plot.get("plant_type")
        plant = PLANT_TYPES.get(pt, {}) if pt else {}

        if status == "empty":
            lines.append(i18n_get_text(language, "farm.plot_empty", num=i+1))
        elif status == "growing":
            ready_at = datetime.fromisoformat(plot["ready_at"])
            remaining = ready_at - now
            days = remaining.days
            hours = remaining.seconds // 3600
            lines.append(i18n_get_text(language, "farm.plot_growing", num=i+1, name=plant.get('name',''), days=days, hours=hours))
        elif status == "ready":
            lines.append(i18n_get_text(language, "farm.plot_ready", num=i+1, emoji=plant.get('emoji','🌿'), name=plant.get('name','')))
        elif status == "dead":
            lines.append(i18n_get_text(language, "farm.plot_dead", num=i+1, name=plant.get('name','')))

    lines.append(i18n_get_text(language, "farm.balance", balance=balance/100))
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
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "farm.button_plant", num=i+1),
                callback_data=f"farm_choose_{i}"
            )])
        elif status == "growing":
            # Water button
            row = []
            water_used = plot.get("water_used_at")
            can_water = not water_used or (now - datetime.fromisoformat(water_used)).total_seconds() >= 86400
            fert_used = plot.get("fertilizer_used_at")
            can_fert = not fert_used or (now - datetime.fromisoformat(fert_used)).total_seconds() >= 86400

            if can_water:
                row.append(InlineKeyboardButton(text=i18n_get_text(language, "farm.button_water", num=i+1), callback_data=f"farm_water_{i}"))
            if can_fert:
                row.append(InlineKeyboardButton(text=i18n_get_text(language, "farm.button_fertilize", num=i+1), callback_data=f"farm_fert_{i}"))
            if row:
                buttons.append(row)
            # Always show dig button for growing plots
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "farm.button_dig", num=i+1),
                callback_data=f"farm_dig_{i}"
            )])
        elif status == "ready":
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "farm.button_harvest", emoji=plant.get('emoji',''), num=i+1, reward=plant.get('reward',0)//100),
                callback_data=f"farm_harvest_{i}"
            )])
        elif status == "dead":
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "farm.button_remove", num=i+1),
                callback_data=f"farm_remove_{i}"
            )])
    
    # Buy plot button
    if plot_count < 9:
        price = 5000  # 50 RUB in kopecks
        remaining = 9 - plot_count
        if balance >= price:
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "farm.button_buy_plot_remaining", remaining=remaining),
                callback_data="farm_buy_plot"
            )])
        else:
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "farm.button_buy_plot_disabled_remaining", remaining=remaining),
                callback_data="farm_noop"
            )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "farm.instructions"),
        url="https://telegra.ph/Instrukciya-Ferma-02-20"
    )])
    buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "farm.back_to_games"), callback_data="games_menu")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
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
        await callback.message.edit_text(
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
        )
        return
    
    await _render_farm(callback, pool)


@router.callback_query(F.data.startswith("farm_choose_"))
async def callback_farm_choose_plant(callback: CallbackQuery, state: FSMContext):
    """Show plant selection screen"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    await callback.answer()
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    plot_id = int(callback.data.split("_")[-1])
    
    buttons = []
    for key, plant in PLANT_TYPES.items():
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "farm.plant_info", emoji=plant['emoji'], name=plant['name'], days=plant['days'], reward=plant['reward']//100),
            callback_data=f"farm_plant_{plot_id}_{key}"
        )])
    buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "farm.back"), callback_data="game_farm")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(
        i18n_get_text(language, "farm.choose_plant_title", num=plot_id+1),
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("farm_plant_"))
async def callback_farm_plant(callback: CallbackQuery, state: FSMContext):
    """Plant a seed"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    await callback.answer()
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    parts = callback.data.split("_")
    plot_id = int(parts[2])
    plant_type = parts[3]
    
    if plant_type not in PLANT_TYPES:
        await callback.answer(i18n_get_text(language, "farm.error_unknown_plant"), show_alert=True)
        return
    
    pool = await database.get_pool()
    if not pool:
        await callback.message.edit_text(
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
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
        await callback.answer(i18n_get_text(language, "farm.error_plot_unavailable"), show_alert=True)
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
        await callback.message.edit_text(
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
        )
        return
    
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    
    plot = None
    for p in farm_plots:
        if p["plot_id"] == plot_id:
            plot = p
            break
    
    if not plot or plot["status"] != "growing":
        await callback.answer(i18n_get_text(language, "farm.error_plot_unavailable"), show_alert=True)
        return
    
    now = datetime.now(timezone.utc)
    water_used = plot.get("water_used_at")
    if water_used:
        water_time = datetime.fromisoformat(water_used)
        if (now - water_time).total_seconds() < 86400:
            await callback.answer(i18n_get_text(language, "farm.error_already_watered"), show_alert=True)
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
        await callback.message.edit_text(
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
        )
        return
    
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    
    plot = None
    for p in farm_plots:
        if p["plot_id"] == plot_id:
            plot = p
            break
    
    if not plot or plot["status"] != "growing":
        await callback.answer(i18n_get_text(language, "farm.error_plot_unavailable"), show_alert=True)
        return
    
    now = datetime.now(timezone.utc)
    fert_used = plot.get("fertilizer_used_at")
    if fert_used:
        fert_time = datetime.fromisoformat(fert_used)
        if (now - fert_time).total_seconds() < 86400:
            await callback.answer(i18n_get_text(language, "farm.error_already_fertilized"), show_alert=True)
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
        await callback.message.edit_text(
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
        )
        return
    
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    
    plot = None
    for p in farm_plots:
        if p["plot_id"] == plot_id:
            plot = p
            break
    
    if not plot or plot["status"] != "ready":
        await callback.answer(i18n_get_text(language, "farm.error_not_ready"), show_alert=True)
        return
    
    plant_type = plot.get("plant_type")
    plant = PLANT_TYPES.get(plant_type, {})
    reward_kopecks = plant.get("reward", 0)
    reward_rubles = reward_kopecks / 100.0
    
    # Add reward to balance
    success = await database.increase_balance(
        telegram_id=telegram_id,
        amount=reward_rubles,
        source="farm_harvest",
        description=f"Farm harvest: {plant.get('name', 'unknown')}"
    )
    
    if not success:
        await callback.answer(i18n_get_text(language, "farm.error_harvest_failed"), show_alert=True)
        return
    
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
    
    # Refresh balance
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    await _render_farm(callback, pool, farm_plots, plot_count, balance)
    
    await callback.answer(i18n_get_text(language, "farm.harvest_success", reward=f"{reward_rubles:.0f}"), show_alert=True)


@router.callback_query(F.data.startswith("farm_remove_") & ~F.data.startswith("farm_remove_confirm_"))
async def callback_farm_remove(callback: CallbackQuery, state: FSMContext):
    """Remove dead plant - show confirmation dialog"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return

    await callback.answer()

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    plot_id = int(callback.data.split("_")[-1])

    # Validate plot is actually dead before showing confirmation
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    plot = next((p for p in farm_plots if p["plot_id"] == plot_id), None)
    if not plot or plot["status"] != "dead":
        await callback.answer(i18n_get_text(language, "farm.error_plot_unavailable"), show_alert=True)
        return

    # Show confirmation dialog
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "farm.remove_yes"),
            callback_data=f"farm_remove_confirm_{plot_id}"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "farm.remove_no"),
            callback_data="game_farm"
        )]
    ])

    await callback.message.edit_text(
        i18n_get_text(language, "farm.remove_confirm"),
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("farm_remove_confirm_"))
async def callback_farm_remove_confirm(callback: CallbackQuery, state: FSMContext):
    """Confirm and execute removal of a dead plant"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return

    await callback.answer()

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    plot_id = int(callback.data.split("_")[-1])

    pool = await database.get_pool()
    if not pool:
        await callback.message.edit_text(
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
        )
        return

    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)

    plot = next((p for p in farm_plots if p["plot_id"] == plot_id), None)

    if not plot or plot["status"] != "dead":
        await callback.answer(i18n_get_text(language, "farm.error_plot_unavailable"), show_alert=True)
        # Refresh farm screen
        await _render_farm(callback, pool, farm_plots, plot_count, balance)
        return

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
        await callback.message.edit_text(
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
        )
        return
    
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    
    if plot_count >= 9:
        await callback.answer(i18n_get_text(language, "farm.max_plots_reached"), show_alert=True)
        return
    
    price = 5000  # 50 RUB in kopecks
    if balance < price:
        await callback.answer(i18n_get_text(language, "farm.insufficient_funds"), show_alert=True)
        return
    
    # Deduct balance
    success = await database.decrease_balance(
        telegram_id=telegram_id,
        amount=50.0,  # 50 RUB
        source="farm_buy_plot",
        description="Farm plot purchase"
    )
    
    if not success:
        await callback.answer(i18n_get_text(language, "farm.buy_plot_error"), show_alert=True)
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
        await callback.message.edit_text(
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
        )
        return
    
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    plot = next((p for p in farm_plots if p["plot_id"] == plot_id), None)
    
    if not plot or plot["status"] != "growing":
        await callback.answer(i18n_get_text(language, "farm.error_dig_unavailable"), show_alert=True)
        return
    
    plant_type = plot.get("plant_type", "")
    plant = PLANT_TYPES.get(plant_type, {})
    plant_name = plant.get("name", "")

    # Show confirmation with inline keyboard
    confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=i18n_get_text(language, "farm.dig_confirm_yes"),
                callback_data=f"farm_dig_confirm_{plot_id}"
            ),
            InlineKeyboardButton(
                text=i18n_get_text(language, "farm.dig_confirm_no"),
                callback_data="game_farm"
            )
        ]
    ])

    await callback.message.edit_text(
        i18n_get_text(language, "farm.dig_confirm_title", name=plant_name, num=plot_id+1),
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
        await callback.message.edit_text(
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
        )
        return
    
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    plot = next((p for p in farm_plots if p["plot_id"] == plot_id), None)
    
    if not plot:
        await callback.answer(i18n_get_text(language, "farm.error_plot_not_found"), show_alert=True)
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
    await callback.answer(i18n_get_text(language, "farm.dig_success"), show_alert=True)
    await _render_farm(callback, pool, farm_plots=farm_plots, 
                       plot_count=plot_count, balance=balance)


@router.callback_query(F.data == "farm_noop")
async def callback_farm_noop(callback: CallbackQuery):
    """No-op handler for disabled buttons"""
    await callback.answer()
