"""Карточка пользователя в админке — общий экран для нескольких разделов.

Её показывают после поиска, после выдачи VIP, после его отзыва и по кнопке
«Назад». Раньше функция лежала в access.py, и модули, которые из него
выделены, тянули бы друг друга по кругу. Здесь она никого не импортирует
из соседних экранов, поэтому кольца не возникает.
"""
import logging
import config
import database
import vpn_utils
from app.i18n import get_text as i18n_get_text
from app.services.admin import service as admin_service
from app.services.admin.exceptions import UserNotFoundError
from app.services.language_service import resolve_user_language
from datetime import datetime, timedelta, timezone
from app.handlers.admin.keyboards import (
    get_admin_back_keyboard,
    get_admin_user_keyboard,
)

logger = logging.getLogger(__name__)


async def _show_admin_user_card(message_or_callback, user_id: int, admin_telegram_id: int):
    """Вспомогательная функция для отображения карточки пользователя администратору"""
    # B3.3 - ADMIN OVERRIDE: Admin operations intentionally bypass system_state checks
    language = await resolve_user_language(admin_telegram_id)
    try:
        overview = await admin_service.get_admin_user_overview(user_id)
    except UserNotFoundError:
        if hasattr(message_or_callback, 'edit_text'):
            await message_or_callback.edit_text(
                i18n_get_text(language, "admin.user_not_found"),
                reply_markup=get_admin_back_keyboard(language),
                parse_mode="HTML",
            )
        else:
            await message_or_callback.answer("❌ Пользователь не найден")
        return
    
    # Получаем доступные действия через admin service
    actions = admin_service.get_admin_user_actions(overview)
    
    # Формируем карточку пользователя (только форматирование)
    text = "👤 Пользователь\n\n"
    text += f"Telegram ID: {overview.user['telegram_id']}\n"
    username_display = overview.user.get('username') or 'не указан'
    text += f"Username: @{username_display}\n"
    
    # Язык
    user_language = overview.user.get('language') or 'ru'
    language_display = i18n_get_text("ru", f"lang.button_{user_language}")
    text += f"Язык: {language_display}\n"
    
    # Дата регистрации
    created_at = overview.user.get('created_at')
    if created_at:
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        created_str = created_at.strftime("%d.%m.%Y %H:%M")
        text += f"Дата регистрации: {created_str}\n"
    else:
        text += "Дата регистрации: —\n"
    
    text += "\n"
    
    # Информация о подписке
    if overview.subscription:
        expires_at = overview.subscription_status.expires_at
        if expires_at:
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M")
        else:
            expires_str = "—"
        
        if overview.subscription_status.is_active:
            text += "Статус подписки: ✅ Активна\n"
        else:
            text += "Статус подписки: ⛔ Истекла\n"
        
        text += f"Срок действия: до {expires_str}\n"
        from vpn_utils import build_sub_url
        sub_url = build_sub_url(overview.telegram_id)
        if sub_url:
            text += f"Ключ подписки:\n<code>{sub_url}</code>\n"
        else:
            text += "Ключ подписки: —\n"
    else:
        text += "Статус подписки: ❌ Нет подписки\n"
        text += "VPN-ключ: —\n"
        text += "Срок действия: —\n"

    # Статистика
    text += f"\nКоличество продлений: {overview.stats['renewals_count']}\n"
    text += f"Количество перевыпусков: {overview.stats['reissues_count']}\n"
    
    # Персональная скидка
    if overview.user_discount:
        discount_percent = overview.user_discount["discount_percent"]
        expires_at_discount = overview.user_discount.get("expires_at")
        if expires_at_discount:
            if isinstance(expires_at_discount, str):
                expires_at_discount = datetime.fromisoformat(expires_at_discount.replace('Z', '+00:00'))
            expires_str = expires_at_discount.strftime("%d.%m.%Y %H:%M")
            text += f"\n🎯 Персональная скидка: {discount_percent}% (до {expires_str})\n"
        else:
            text += f"\n🎯 Персональная скидка: {discount_percent}% (бессрочно)\n"
    
    # VIP-статус
    if overview.is_vip:
        text += f"\n👑 VIP-��татус: активен\n"

    # Remnawave трафик (краткая сводка)
    _rmn_uuid = await database.get_remnawave_uuid(user_id)
    if _rmn_uuid:
        try:
            from app.services import remnawave_api
            _traffic = await remnawave_api.get_user_traffic(_rmn_uuid)
            if _traffic:
                _used = _traffic.get("usedTrafficBytes", 0)
                _limit = _traffic.get("trafficLimitBytes", 0)
                _remaining = max(0, _limit - _used)
                def _fmt(b):
                    return f"{b / 1024**3:.1f} Г��" if b >= 1024**3 else f"{b / 1024**2:.0f} МБ"
                text += f"\n📊 Трафик обхода: {_fmt(_used)} / {_fmt(_limit)} (ост. {_fmt(_remaining)})\n"
        except Exception:
            pass

    # Отображаем карточку
    sub_type = (overview.subscription.get("subscription_type") or "basic").strip().lower() if overview.subscription else "basic"
    if sub_type not in config.VALID_SUBSCRIPTION_TYPES:
        sub_type = "basic"
    keyboard = get_admin_user_keyboard(
        has_active_subscription=overview.subscription_status.is_active,
        user_id=overview.user["telegram_id"],
        has_discount=overview.user_discount is not None,
        is_vip=overview.is_vip,
        subscription_type=sub_type,
        language=language
    )
    
    if hasattr(message_or_callback, 'edit_text'):
        await message_or_callback.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await message_or_callback.answer(text, reply_markup=keyboard, parse_mode="HTML")
