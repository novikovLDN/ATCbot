"""
Bowling game handler - 7-day cooldown, Telegram dice 🎳, +7 days subscription reward on strike
"""
import logging
import asyncio
from datetime import datetime, timedelta, timezone

from aiogram import Router, F
from aiogram.types import CallbackQuery

import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.keyboards import get_back_keyboard

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "game_bowl")
async def callback_game_bowl(callback: CallbackQuery):
    """Bowling game - check cooldown, play if available, grant +7 days on strike"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    try:
        # Get user's last play time
        pool = await database.get_pool()
        if not pool:
            error_text = i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable")
            await callback.answer(error_text, show_alert=True)
            return
        
        async with pool.acquire() as conn:
            user_row = await conn.fetchrow(
                "SELECT game_last_played FROM users WHERE telegram_id = $1",
                telegram_id
            )
            
            if not user_row:
                # User doesn't exist in DB yet - create them
                await conn.execute(
                    "INSERT INTO users (telegram_id, language) VALUES ($1, $2) ON CONFLICT (telegram_id) DO NOTHING",
                    telegram_id, language
                )
                game_last_played = None
            else:
                game_last_played_raw = user_row.get("game_last_played")
                game_last_played = database._from_db_utc(game_last_played_raw) if game_last_played_raw else None
            
            now = datetime.now(timezone.utc)
            
            # Check cooldown (7 days)
            if game_last_played:
                time_since_last_play = now - game_last_played
                cooldown = timedelta(days=7)
                
                if time_since_last_play < cooldown:
                    # Still on cooldown - calculate remaining time
                    remaining = cooldown - time_since_last_play
                    days = remaining.days
                    hours = remaining.seconds // 3600
                    
                    message_text = (
                        f"Боулинг-клуб закрыт 🎳\n"
                        f"Следующая игра доступна через: {days}д {hours}ч"
                    )
                    
                    keyboard = get_back_keyboard(language)
                    await callback.message.edit_text(message_text, reply_markup=keyboard)
                    await callback.answer()
                    logger.info("GAME_BOWL [user=%s, status=cooldown, days=%s, hours=%s]", telegram_id, days, hours)
                    return
            
            # Play the game
            # Update game_last_played immediately (before roll)
            await conn.execute(
                "UPDATE users SET game_last_played = $1 WHERE telegram_id = $2",
                database._to_db_utc(now), telegram_id
            )
            
            # Send Telegram dice animation
            dice_message = await callback.message.answer_dice(emoji="🎳")
            dice_value = dice_message.dice.value
            # Bowling dice: value 6 = strike, values 1-5 = no strike
            
            # Wait for animation to show before sending result
            await asyncio.sleep(4)
            
            if dice_value == 6:
                # STRIKE! Grant +7 days subscription
                logger.info("GAME_BOWL [user=%s, strike=True, dice_value=6]", telegram_id)
                
                try:
                    # Grant 7 days subscription
                    result = await database.grant_access(
                        telegram_id=telegram_id,
                        duration=timedelta(days=7),
                        source="game_strike"
                    )
                    
                    message_text = (
                        "🎳 Страйк! Все кегли сбиты!\n\n"
                        "🎉 Поздравляем! Вы выиграли +7 дней подписки!"
                    )
                    
                    logger.info(
                        "GAME_BOWL [user=%s, strike=True, dice_value=6, grant_success=True, uuid=%s]",
                        telegram_id, result.get("uuid", "N/A")[:8] if result.get("uuid") else "N/A"
                    )
                except Exception as e:
                    logger.error("GAME_BOWL [user=%s, strike=True, grant_error=%s]", telegram_id, str(e))
                    message_text = (
                        "🎳 Страйк! Все кегли сбиты!\n\n"
                        "🎉 Поздравляем! Вы выиграли +7 дней подписки!\n\n"
                        "⚠️ Ошибка при начислении подписки. Обратитесь в поддержку."
                    )
            else:
                # No strike - dice_value is 1-5
                message_text = (
                    f"🎳 Вы сбили {dice_value} кеглей из 10...\n\n"
                    f"Увы, не страйк 😔 Попробуйте снова через 7 дней!"
                )
                logger.info("GAME_BOWL [user=%s, strike=False, dice_value=%s]", telegram_id, dice_value)
            
            keyboard = get_back_keyboard(language)
            await callback.message.answer(message_text, reply_markup=keyboard)
            await callback.answer()
            
    except Exception as e:
        logger.exception("GAME_BOWL [user=%s, error=%s]", telegram_id, str(e))
        error_text = i18n_get_text(language, "errors.generic", "Произошла ошибка. Попробуйте позже.")
        await callback.answer(error_text, show_alert=True)
