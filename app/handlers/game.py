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
from app.handlers.common.utils import safe_edit_text

router = Router()
logger = logging.getLogger(__name__)

# Plant types for Farm game
PLANT_TYPES = {
    # Existing 6 cultures — untouched balance, classic line-up
    "tomato":    {"emoji": "🍅", "name": "Томаты",      "days": 3,  "reward": 500},
    "potato":    {"emoji": "🥔", "name": "Картофель",   "days": 5,  "reward": 1000},
    "carrot":    {"emoji": "🥕", "name": "Морковь",     "days": 7,  "reward": 1000},
    "cactus":    {"emoji": "🌵", "name": "Кактус",      "days": 10, "reward": 1500},
    "apple":     {"emoji": "🍏", "name": "Яблоня",      "days": 8,  "reward": 1500},
    "lavender":  {"emoji": "💜", "name": "Лаванда",     "days": 6,  "reward": 2000},
    # Fast cultures — daily/short cycle
    "greens":    {"emoji": "🌱", "name": "Зелень",      "days": 1,  "reward": 200},
    "pepper":    {"emoji": "🌶", "name": "Перчик",      "days": 4,  "reward": 800},
    # Mid cultures
    "cucumber":  {"emoji": "🥒", "name": "Огурец",      "days": 5,  "reward": 1200},
    "sunflower": {"emoji": "🌻", "name": "Подсолнух",   "days": 6,  "reward": 1400},
    "strawberry":{"emoji": "🍓", "name": "Клубника",    "days": 7,  "reward": 1800},
    # Trees — long cycle, premium reward
    "grape":     {"emoji": "🍇", "name": "Виноград",    "days": 12, "reward": 3200},
    "cherry":    {"emoji": "🍒", "name": "Вишня",       "days": 13, "reward": 3600},
    "lemon":     {"emoji": "🍋", "name": "Лимонное дерево", "days": 16, "reward": 4800},
    "oak":       {"emoji": "🌳", "name": "Дуб",         "days": 21, "reward": 7000},
}
# reward is in kopecks (200 = 2 RUB, 7000 = 70 RUB)


# Storm shield price tiers (kopecks) — by plant reward
# ≤ 25 RUB → 10 RUB,  26–40 RUB → 20 RUB,  > 40 RUB → 30 RUB
def storm_shield_price_kopecks(plant_reward_kopecks: int) -> int:
    if plant_reward_kopecks <= 2500:
        return 1000
    if plant_reward_kopecks <= 4000:
        return 2000
    return 3000


# Farm plot purchase price (kopecks) — applies to NEW plot purchases only.
# Existing users keep every plot they already bought; never decremented.
FARM_PLOT_PRICE_KOPECKS = 6000  # 60 RUB
FARM_MAX_PLOTS = 9


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
    subscription = await database.get_subscription(telegram_id)
    if not subscription:
        await callback.answer(
            "🎮 Игровой клуб доступен только для подписчиков!\n"
            "Оформите подписку и получите доступ ко всем играм 🎯",
            show_alert=True,
        )
        return

    await callback.answer()

    language = await resolve_user_language(telegram_id)

    text = i18n_get_text(language, "games.menu_title")

    # Photo screen: drop previous message (text or photo) and send a fresh
    # photo-with-caption.  _send_screen_photo falls back to text if needed.
    try:
        await callback.message.delete()
    except Exception:
        pass
    from app.handlers.common.screens import _send_screen_photo, GAMES_PHOTO_FILE_ID
    await _send_screen_photo(
        callback.bot, telegram_id, GAMES_PHOTO_FILE_ID, text,
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
            await safe_edit_text(callback.message,
                i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
                reply_markup=get_back_keyboard(language),
                parse_mode="HTML",
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
                    await safe_edit_text(callback.message,
                        text,
                        reply_markup=get_games_back_keyboard(language),
                        parse_mode="HTML",
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
                await safe_edit_text(callback.message,paywall_text, reply_markup=keyboard, parse_mode="HTML")
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
                # Preserve current tariff (don't downgrade Plus to Basic)
                sub = await database.get_subscription(telegram_id)
                current_tariff = (sub.get("subscription_type") or "basic").strip().lower() if sub else "basic"
                result = await database.grant_access(
                    telegram_id=telegram_id,
                    duration=timedelta(days=7),
                    source="game_strike",
                    tariff=current_tariff,
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
        await safe_edit_text(callback.message,
            i18n_get_text(language, "errors.generic", "Произошла ошибка. Попробуйте позже."),
            reply_markup=get_games_back_keyboard(language),
            parse_mode="HTML",
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
            await safe_edit_text(callback.message,
                i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
                reply_markup=get_games_back_keyboard(language),
                parse_mode="HTML",
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
                    await safe_edit_text(callback.message,
                        text,
                        reply_markup=get_games_back_keyboard(language),
                        parse_mode="HTML",
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
                await safe_edit_text(callback.message,paywall_text, reply_markup=keyboard, parse_mode="HTML")
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
            # Preserve current tariff (don't downgrade Plus to Basic)
            sub = await database.get_subscription(telegram_id)
            current_tariff = (sub.get("subscription_type") or "basic").strip().lower() if sub else "basic"
            result = await database.grant_access(
                telegram_id=telegram_id,
                duration=timedelta(days=dice_value),
                source="game_dice",
                tariff=current_tariff,
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
        await safe_edit_text(callback.message,
            i18n_get_text(language, "errors.generic", "Произошла ошибка. Попробуйте позже."),
            reply_markup=get_games_back_keyboard(language),
            parse_mode="HTML",
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
    
    text = i18n_get_text(language, "games.bomber_rules", "💣 Бомбер\n\nПравила:\n• Размещайте бомбы на поле, избегая мин бота\n• Если наступите на свою бомбу — взрыв! 💥\n• Если наступите на мину бота — взрыв! 💥\n• Нажмите 'Завершить' чтобы безопасно выйти\n\nУдачи! 🍀")
    
    await safe_edit_text(callback.message,
        text,
        reply_markup=create_bomber_grid_keyboard(mines, player_bombs, language),
        parse_mode="HTML",
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
            await safe_edit_text(callback.message,
                text,
                reply_markup=get_games_back_keyboard(language),
                parse_mode="HTML",
            )
            logger.info("GAME_BOMBER [user=%s] self_destruct cell=%s", telegram_id, cell_idx)
            return
        
        # Check if player clicked a bot mine
        if cell_idx in mines:
            # Game over!
            await state.clear()
            text = i18n_get_text(language, "games.bomber_mine_exploded", "💥 БУМ! Вы подорвались на мине бота!\n\nИгра окончена. Попробуйте ещё раз!")
            await safe_edit_text(callback.message,
                text,
                reply_markup=create_bomber_grid_keyboard(mines, player_bombs, language, game_over=True),
                parse_mode="HTML",
            )
            await asyncio.sleep(2)
            await safe_edit_text(callback.message,
                text,
                reply_markup=get_games_back_keyboard(language),
                parse_mode="HTML",
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
        await safe_edit_text(callback.message,
            i18n_get_text(language, "errors.generic", "Произошла ошибка. Попробуйте позже."),
            reply_markup=get_games_back_keyboard(language),
            parse_mode="HTML",
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
        
        await safe_edit_text(callback.message,
            text,
            reply_markup=get_games_back_keyboard(language),
            parse_mode="HTML",
        )
        
        logger.info("GAME_BOMBER [user=%s] safe_exit bombs=%s", telegram_id, bomb_count)
        
    except Exception as e:
        logger.exception("GAME_BOMBER_EXIT [user=%s] error=%s", telegram_id, e)
        await state.clear()
        await safe_edit_text(callback.message,
            i18n_get_text(language, "errors.generic", "Произошла ошибка. Попробуйте позже."),
            reply_markup=get_games_back_keyboard(language),
            parse_mode="HTML",
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
    
    # Add reward to balance
    success = await database.increase_balance(
        telegram_id=telegram_id,
        amount=reward_rubles,
        source="farm_harvest",
        description=f"Farm harvest: {plant.get('name', 'unknown')}"
    )
    
    if not success:
        await callback.answer("Ошибка при начислении награды", show_alert=True)
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
        [InlineKeyboardButton(text="📲 СБП (+11%)", callback_data=f"farm_shield_sbp:{plot_id}")],
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

    # Credit balance and reset plot to empty (mirrors normal harvest cleanup).
    ok = await database.increase_balance(
        telegram_id=telegram_id,
        amount=half_reward_kopecks / 100.0,
        source="farm_early_harvest",
        description=f"Early harvest plot {plot_id} ({plant.get('name','')})",
    )
    if not ok:
        await callback.answer("Не удалось зачислить награду.", show_alert=True)
        return

    for p in farm_plots:
        if int(p.get("plot_id", -1)) == plot_id:
            p["status"] = "empty"
            p["plant_type"] = None
            p["planted_at"] = None
            p["ready_at"] = None
            p["dead_at"] = None
            p["notified_ready"] = False
            p["notified_12h"] = False
            p["notified_dead"] = False
            p["water_used_at"] = None
            p["fertilizer_used_at"] = None
            p["storm_shielded"] = False
            break
    await database.save_farm_plots(telegram_id, farm_plots)

    await callback.answer(
        f"🚜 Собрано {plant.get('emoji','')} незрелым: +{half_reward_kopecks // 100} ₽",
        show_alert=True,
    )
    pool = await database.get_pool()
    await _render_farm(callback, pool)
