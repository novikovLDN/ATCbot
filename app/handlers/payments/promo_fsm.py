"""
Promo code FSM message handler: PromoCodeInput.waiting_for_promo
"""
import logging
import time

from aiogram import Router
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language, DEFAULT_LANGUAGE
from app.utils.security import (
    validate_telegram_id,
    validate_promo_code,
    log_security_warning,
)
from app.handlers.common.guards import ensure_db_ready_message
from app.handlers.common.states import PromoCodeInput
from app.handlers.common.utils import get_promo_session

payments_router = Router()
logger = logging.getLogger(__name__)


@payments_router.message(PromoCodeInput.waiting_for_promo)
async def process_promo_code(message: Message, state: FSMContext):
    """Обработчик ввода промокода - работает ТОЛЬКО в состоянии waiting_for_promo"""
    # CRITICAL FIX: Дополнительная проверка state для защиты от спама
    current_state = await state.get_state()
    if current_state != PromoCodeInput.waiting_for_promo.state:
        # Пользователь уже покинул экран ввода промокода - игнорируем сообщение
        return
    
    # STEP 4 — PART A: INPUT TRUST BOUNDARIES
    # Validate telegram_id
    telegram_id = message.from_user.id
    is_valid, error = validate_telegram_id(telegram_id)
    if not is_valid:
        log_security_warning(
            event="Invalid telegram_id in promo code input",
            telegram_id=telegram_id,
            correlation_id=str(message.message_id) if hasattr(message, 'message_id') else None,
            details={"error": error}
        )
        language = await resolve_user_language(message.from_user.id)
        await message.answer(i18n_get_text(language, "errors.try_later"))
        await state.clear()
        return
    
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_message(message):
        await state.clear()
        return
    
    language = await resolve_user_language(telegram_id)
    
    # ⛔ Защита от non-text апдейтов (callback / invoice / system)
    if not message.text:
        from app.handlers.common.keyboards import _get_promo_error_keyboard
        await message.answer(
            i18n_get_text(language, "buy.promo_enter_text_hint"),
            reply_markup=_get_promo_error_keyboard(language)
        )
        return

    promo_code = message.text.strip().upper()
    
    # STEP 4 — PART A: INPUT TRUST BOUNDARIES
    # Validate message text format
    is_valid_promo, promo_error = validate_promo_code(promo_code)
    if not is_valid_promo:
        # Только логируем SECURITY_WARNING если пользователь действительно в FSM
        log_security_warning(
            event="Invalid promo code format",
            telegram_id=telegram_id,
            correlation_id=str(message.message_id) if hasattr(message, 'message_id') else None,
            details={"error": promo_error, "promo_code_preview": promo_code[:20] if promo_code else None}
        )
        from app.handlers.common.keyboards import _get_promo_error_keyboard
        text = i18n_get_text(language, "main.invalid_promo")
        await message.answer(text, reply_markup=_get_promo_error_keyboard(language))
        return
    
    # КРИТИЧНО: Проверяем активную промо-сессию
    promo_session = await get_promo_session(state)
    if promo_session and promo_session.get("promo_code") == promo_code:
        # Промокод уже применён в активной сессии - показываем сообщение
        expires_at = promo_session.get("expires_at", 0)
        expires_in = max(0, int(expires_at - time.time()))
        text = i18n_get_text(language, "main.promo_applied")
        await message.answer(text)
        # CRITICAL FIX: Используем каноничный экран тарифов вместо локального render
        from app.handlers.common.screens import show_tariffs_main_screen
        await show_tariffs_main_screen(message, state)
        return
    
    # CRITICAL FIX: Используем validate_promocode_atomic для валидации без инкремента
    # Промокод будет потреблен только при успешной оплате
    result = await database.validate_promocode_atomic(promo_code)
    if result["success"]:
        promo_data = result["promo_data"]
        # Промокод валиден
        discount_percent = promo_data["discount_percent"]
        
        # КРИТИЧНО: Создаём промо-сессию с TTL 5 минут
        from app.handlers.common.utils import create_promo_session
        await create_promo_session(
            state=state,
            promo_code=promo_code,
            discount_percent=discount_percent,
            telegram_id=telegram_id,
            ttl_seconds=300
        )
        
        # КРИТИЧНО: НЕ отменяем pending покупки - промо-сессия независима от покупки
        
        # CRITICAL FIX: Очищаем FSM state после успешного применения промокода
        await state.set_state(None)
        
        text = i18n_get_text(language, "main.promo_applied")
        await message.answer(text)
        
        logger.info(
            f"promo_applied: user={telegram_id}, promo_code={promo_code}, "
            f"discount_percent={discount_percent}%, old_purchases_cancelled=True"
        )
        
        # CRITICAL FIX: Используем каноничный экран тарифов вместо локального render
        # Цены будут автоматически пересчитаны с промокодом при выборе тарифа
        from app.handlers.common.screens import show_tariffs_main_screen
        await show_tariffs_main_screen(message, state)
    else:
        # Промокод невалиден
        error_reason = result.get("error", "invalid")
        from app.handlers.common.keyboards import _get_promo_error_keyboard
        text = i18n_get_text(language, "main.invalid_promo")
        await message.answer(text, reply_markup=_get_promo_error_keyboard(language))
        logger.info(
            f"promo_validation_failed: user={telegram_id}, promo_code={promo_code}, "
            f"reason={error_reason}"
        )
