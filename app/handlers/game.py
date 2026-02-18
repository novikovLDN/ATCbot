"""
Bowling game handler — Telegram Dice 🎳, 7-day cooldown, strike = +7 days subscription.
Webhook-safe: callback.answer() before long ops; no DB connection held during dice animation.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import Bot

import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.keyboards import get_back_keyboard

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "game_bowl")
async def callback_game_bowl(callback: CallbackQuery, bot: Bot = None):
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
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS game_last_played TIMESTAMPTZ"
            )
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
                        reply_markup=get_back_keyboard(language),
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
                reply_markup=get_back_keyboard(language),
                parse_mode="HTML",
            )
        else:
            text = (
                f"🎳 Вы сбили {dice_value} кеглей из 10.\n\n"
                "Увы, не страйк 😔 Попробуйте снова через 7 дней!"
            )
            logger.info("GAME_BOWL [user=%s] strike=False dice_value=%s", telegram_id, dice_value)
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=get_back_keyboard(language),
                parse_mode="HTML",
            )

    except Exception as e:
        logger.exception("GAME_BOWL [user=%s] error=%s", telegram_id, e)
        await callback.message.edit_text(
            i18n_get_text(language, "errors.generic", "Произошла ошибка. Попробуйте позже."),
            reply_markup=get_back_keyboard(language),
        )
