"""Подарки пользователя: список купленных и карточка одного подарка.

ЧТО ЗДЕСЬ
    Экран «Мои подарки» с постраничной сеткой и детальный экран со
    ссылкой и кнопкой «Отправить».

ПОЧЕМУ ВЫДЕЛЕНО
    Только чтение: ни оплаты, ни создания подарков. Правится по своему
    поводу — вёрстка сетки, пагинация, статусы.

ЧТО ЛЕГКО СЛОМАТЬ
    Номер страницы едет в callback_data кнопки «Назад к подаркам»
    (gift_detail:<id>:<page>). Потеряете его — человек с пятой страницы
    вернётся на первую и будет искать свой подарок заново.

    Ссылка активации показывается только для неактивированного подарка.
    Показав её для активированного, вы дадите повод думать, что подарком
    ещё можно поделиться.
"""
import logging
import math
from urllib.parse import quote

import database
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.utils import safe_edit_text
from app.handlers.callbacks.gift.formatting import _period_display, _tariff_display_name

router = Router()
logger = logging.getLogger(__name__)


GIFTS_PER_PAGE = 6  # 3 rows × 2 columns


@router.callback_query(F.data.startswith("my_gifts:"))
async def callback_my_gifts(callback: CallbackQuery):
    """Экран «Мои подарки» — карусель купленных подарков."""
    if not await ensure_db_ready_callback(callback):
        return

    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    page_str = callback.data.split(":")[1]
    try:
        page = int(page_str)
    except ValueError:
        page = 0

    gifts = await database.get_user_gifts(telegram_id)

    if not gifts:
        text = i18n_get_text(language, "gift.my_gifts_empty", "🎁 У вас пока нет подарков.\n\nВы можете приобрести подарок в главном меню.")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "gift.buy_gift_btn", "🎁 Подарить подписку"),
                callback_data="gift_subscription"
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "gift.back_to_profile", "👤 Вернуться в профиль"),
                callback_data="menu_profile"
            )],
        ])
        await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML", bot=callback.bot)
        return

    total_pages = math.ceil(len(gifts) / GIFTS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start = page * GIFTS_PER_PAGE
    page_gifts = gifts[start:start + GIFTS_PER_PAGE]

    text = i18n_get_text(language, "gift.my_gifts_title", "🎁 <b>Мои подарки</b>")
    if total_pages > 1:
        text += f"\n\n📄 {page + 1}/{total_pages}"

    # Build 2-column grid (up to 3 rows)
    buttons = []
    for i in range(0, len(page_gifts), 2):
        row = []
        for gift in page_gifts[i:i + 2]:
            tariff_name = _tariff_display_name(gift["tariff"])
            period_text = _period_display(gift["period_days"])
            status_icon = "✅" if gift["status"] == "activated" else "❌"
            btn_text = f"{tariff_name} {period_text} {status_icon}"
            row.append(InlineKeyboardButton(
                text=btn_text,
                callback_data=f"gift_detail:{gift['id']}:{page}"
            ))
        buttons.append(row)

    # Pagination: Назад / Дальше
    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(
                text=i18n_get_text(language, "gift.page_prev", "⬅️ Назад"),
                callback_data=f"my_gifts:{page - 1}"
            ))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(
                text=i18n_get_text(language, "gift.page_next", "Дальше ➡️"),
                callback_data=f"my_gifts:{page + 1}"
            ))
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "gift.back_to_profile", "👤 Вернуться в профиль"),
        callback_data="menu_profile"
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML", bot=callback.bot)


# ====================================================================================
# GIFT DETAIL: Экран отдельного подарка
# ====================================================================================

@router.callback_query(F.data.startswith("gift_detail:"))
async def callback_gift_detail(callback: CallbackQuery):
    """Детальный экран подарка — ссылка + кнопка «Отправить»."""
    if not await ensure_db_ready_callback(callback):
        return

    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    parts = callback.data.split(":")
    try:
        gift_id = int(parts[1])
        back_page = int(parts[2]) if len(parts) > 2 else 0
    except (ValueError, IndexError):
        await callback.answer(i18n_get_text(language, "errors.tariff"), show_alert=True)
        return

    # Fetch all user gifts and find the one by id
    gifts = await database.get_user_gifts(telegram_id)
    gift = next((g for g in gifts if g["id"] == gift_id), None)

    if not gift:
        await callback.answer(i18n_get_text(language, "gift.error_not_found"), show_alert=True)
        return

    tariff_name = _tariff_display_name(gift["tariff"])
    period_text = _period_display(gift["period_days"])
    gift_code = gift["gift_code"]

    bot_info = await callback.bot.get_me()
    bot_username = bot_info.username
    gift_link = f"https://t.me/{bot_username}?start=gift_{gift_code}"

    if gift["status"] == "activated":
        status_text = i18n_get_text(language, "gift.status_activated", "✅ Активирован")
        text = i18n_get_text(
            language, "gift.detail_activated",
            f"🎁 <b>{tariff_name} — {period_text}</b>\n\n{status_text}",
            tariff_name=tariff_name,
            period=period_text,
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "gift.back_to_gifts", "🎁 Назад к подаркам"),
                callback_data=f"my_gifts:{back_page}"
            )],
        ])
    else:
        status_text = i18n_get_text(language, "gift.status_pending", "❌ Не активирован")
        text = i18n_get_text(
            language, "gift.detail_pending",
            f"🎁 <b>Отправьте подарок близкому!</b>\n\n📦 Тариф: {tariff_name}\n⏳ Срок: {period_text}\n\n{status_text}\n\n🔗 Ссылка для активации:\n<code>{gift_link}</code>",
            tariff_name=tariff_name,
            period=period_text,
            gift_link=gift_link,
        )

        share_text = i18n_get_text(
            language, "gift.share_text",
            tariff_name=tariff_name,
            period=period_text,
            gift_link=gift_link,
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "gift.btn_share", "📤 Отправить ссылку"),
                url=f"https://t.me/share/url?url={quote(gift_link)}&text={quote(share_text)}",
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "gift.back_to_gifts", "🎁 Назад к подаркам"),
                callback_data=f"my_gifts:{back_page}"
            )],
        ])

    await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML", bot=callback.bot)
