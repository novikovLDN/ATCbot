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

# Plant types for Farm game
# 2026-06-08: rewards reduced to 75% (-25%) and ripening times
# extended to ×1.5 of the original. Combined drop in farm passive
# income is ~50% so 4-plot Oak no longer covers a basic VPN month.
# Rewards in kopecks, always a multiple of 100 (no kopeck tails in UI).
PLANT_TYPES = {
    # Existing 6 cultures
    "tomato":    {"emoji": "🍅", "name": "Томаты",      "days": 5,  "reward": 400},
    "potato":    {"emoji": "🥔", "name": "Картофель",   "days": 8,  "reward": 800},
    "carrot":    {"emoji": "🥕", "name": "Морковь",     "days": 11, "reward": 800},
    "cactus":    {"emoji": "🌵", "name": "Кактус",      "days": 15, "reward": 1100},
    "apple":     {"emoji": "🍏", "name": "Яблоня",      "days": 12, "reward": 1100},
    "lavender":  {"emoji": "💜", "name": "Лаванда",     "days": 9,  "reward": 1500},
    # Fast cultures — daily/short cycle
    "greens":    {"emoji": "🌱", "name": "Зелень",      "days": 2,  "reward": 200},
    "pepper":    {"emoji": "🌶", "name": "Перчик",      "days": 6,  "reward": 600},
    # Mid cultures
    "cucumber":  {"emoji": "🥒", "name": "Огурец",      "days": 8,  "reward": 900},
    "sunflower": {"emoji": "🌻", "name": "Подсолнух",   "days": 9,  "reward": 1100},
    "strawberry":{"emoji": "🍓", "name": "Клубника",    "days": 11, "reward": 1400},
    # Trees — long cycle, premium reward
    "grape":     {"emoji": "🍇", "name": "Виноград",    "days": 18, "reward": 2400},
    "cherry":    {"emoji": "🍒", "name": "Вишня",       "days": 20, "reward": 2700},
    "lemon":     {"emoji": "🍋", "name": "Лимонное дерево", "days": 24, "reward": 3600},
    "oak":       {"emoji": "🌳", "name": "Дуб",         "days": 32, "reward": 5300},
}


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
    language = await resolve_user_language(telegram_id)
    if not subscription:
        await callback.answer(
            i18n_get_text(language, "games.menu_paywall"),
            show_alert=True,
        )
        return

    await callback.answer()

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

            # Захват кулдауна атомарным UPDATE: условие проверяется той же
            # операцией, что и запись. Раньше между SELECT выше и этим UPDATE
            # не было ни транзакции, ни блокировки, поэтому параллельные клики
            # проходили проверку оба и получали по гранту подписки каждый.
            claimed = await conn.fetchval(
                """
                UPDATE users
                   SET game_last_played = $1
                 WHERE telegram_id = $2
                   AND (game_last_played IS NULL OR game_last_played <= $3)
                RETURNING telegram_id
                """,
                database._to_db_utc(now),
                telegram_id,
                database._to_db_utc(now - cooldown),
            )
            if claimed is None:
                logger.info(
                    "GAME_BOWL [user=%s] cooldown_race_blocked — параллельный клик уже занял попытку",
                    telegram_id,
                )
                await callback.answer(
                    i18n_get_text(language, "games.race_blocked"), show_alert=True,
                )
                return

        dice_message = await bot.send_dice(chat_id=chat_id, emoji="🎳")
        await asyncio.sleep(4)
        dice_value = dice_message.dice.value

        if dice_value == 6:
            try:
                # Месячный потолок: без него игры раздавали порядка 12,5 дней
                # подписки в месяц и заменяли собой покупку.
                granted_days, already, _remaining = await database.check_game_days_cap(
                    telegram_id, 7, config.GAME_MONTHLY_DAYS_CAP
                )
                if granted_days <= 0:
                    logger.info(
                        "GAME_BOWL [user=%s] cap_reached granted_this_month=%s cap=%s",
                        telegram_id, already, config.GAME_MONTHLY_DAYS_CAP,
                    )
                    await safe_edit_text(
                        callback.message,
                        i18n_get_text(
                            language, "games.monthly_cap_reached",
                            "🎳 <b>Страйк!</b>\n\nНо месячный лимит бонусных дней уже выбран "
                            "({cap} дн.). Следующие бонусы — в новом месяце.",
                        ).format(cap=config.GAME_MONTHLY_DAYS_CAP),
                        reply_markup=get_games_back_keyboard(language),
                        parse_mode="HTML",
                    )
                    return

                # Preserve current tariff (don't downgrade Plus to Basic)
                sub = await database.get_subscription(telegram_id)
                current_tariff = (sub.get("subscription_type") or "basic").strip().lower() if sub else "basic"
                result = await database.grant_access(
                    telegram_id=telegram_id,
                    duration=timedelta(days=granted_days),
                    source="game_strike",
                    tariff=current_tariff,
                )
                await database.log_game_reward_days(telegram_id, granted_days, "bowling")
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

            # Захват кулдауна атомарным UPDATE — см. комментарий в боулинге.
            # Без условия в самом UPDATE параллельные клики получали по гранту
            # подписки каждый, а кубики дают до 6 дней за раз.
            claimed = await conn.fetchval(
                """
                UPDATE users
                   SET dice_last_played = $1
                 WHERE telegram_id = $2
                   AND (dice_last_played IS NULL OR dice_last_played <= $3)
                RETURNING telegram_id
                """,
                database._to_db_utc(now),
                telegram_id,
                database._to_db_utc(now - cooldown),
            )
            if claimed is None:
                logger.info(
                    "GAME_DICE [user=%s] cooldown_race_blocked — параллельный клик уже занял попытку",
                    telegram_id,
                )
                await callback.answer(
                    i18n_get_text(language, "games.race_blocked"), show_alert=True,
                )
                return

        dice_message = await bot.send_dice(chat_id=chat_id, emoji="🎲")
        await asyncio.sleep(2)
        dice_value = dice_message.dice.value

        # Grant days equal to dice value (1-6)
        try:
            # Месячный потолок игровых наград — см. комментарий в боулинге.
            granted_days, already, _remaining = await database.check_game_days_cap(
                telegram_id, dice_value, config.GAME_MONTHLY_DAYS_CAP
            )
            if granted_days <= 0:
                logger.info(
                    "GAME_DICE [user=%s] cap_reached granted_this_month=%s cap=%s",
                    telegram_id, already, config.GAME_MONTHLY_DAYS_CAP,
                )
                await bot.send_message(
                    chat_id=chat_id,
                    text=i18n_get_text(
                        language, "games.monthly_cap_reached",
                        "🎲 Выпало: {value}!\n\nНо месячный лимит бонусных дней уже выбран "
                        "({cap} дн.). Следующие бонусы — в новом месяце.",
                    ).format(value=dice_value, cap=config.GAME_MONTHLY_DAYS_CAP),
                    reply_markup=get_games_back_keyboard(language),
                    parse_mode="HTML",
                )
                return

            # Preserve current tariff (don't downgrade Plus to Basic)
            sub = await database.get_subscription(telegram_id)
            current_tariff = (sub.get("subscription_type") or "basic").strip().lower() if sub else "basic"
            result = await database.grant_access(
                telegram_id=telegram_id,
                duration=timedelta(days=granted_days),
                source="game_dice",
                tariff=current_tariff,
            )
            await database.log_game_reward_days(telegram_id, granted_days, "dice")
            end_dt = result.get("subscription_end")
            if end_dt and hasattr(end_dt, "strftime"):
                end_str = end_dt.strftime("%d.%m.%Y")
            else:
                end_str = "—"
            text = i18n_get_text(language, "games.dice_success", "🎲 Выпало: {value}!\n\n🎉 Вам начислено {value} дней подписки!\n\nВаша подписка действует до: {date}").format(value=granted_days, date=end_str)
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
