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
                "UPDATE users SET game_last_played = $1 WHERE telegram_id = $2",
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
