"""
STAGE-only admin tool: browse and delete users from the DB.

Loads in every environment, but every handler refuses to act unless
config.IS_STAGE is True — and the dashboard hides the entry point in
prod/local. Delete reuses the existing admin:delete_user flow (with its
confirmation step) so cascading delete logic stays in one place.
"""
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import config
import database
from app.handlers.common.utils import safe_edit_text

admin_stage_users_router = Router()
logger = logging.getLogger(__name__)

_PAGE_SIZE = 20


def _is_authorized(callback: CallbackQuery) -> bool:
    return (
        callback.from_user.id == config.ADMIN_TELEGRAM_ID
        and config.IS_STAGE
    )


async def _list_users_page(offset: int, limit: int):
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT telegram_id, username, language, created_at "
            "FROM users ORDER BY created_at DESC NULLS LAST, telegram_id DESC "
            "LIMIT $1 OFFSET $2",
            limit, offset,
        )
        total = await conn.fetchval("SELECT COUNT(*) FROM users")
    return rows, total or 0


async def _render_users_page(callback: CallbackQuery, page: int):
    page = max(0, page)
    offset = page * _PAGE_SIZE
    rows, total = await _list_users_page(offset, _PAGE_SIZE)

    if total == 0:
        await safe_edit_text(
            callback.message,
            "🧪 <b>STAGE · Пользователи</b>\n\nВ БД пользователей нет.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="← Назад", callback_data="admin:main")],
            ]),
            bot=callback.bot, parse_mode="HTML",
        )
        return

    pages = (total + _PAGE_SIZE - 1) // _PAGE_SIZE
    page = min(page, pages - 1)

    text = (
        "🧪 <b>STAGE · Пользователи</b>\n"
        f"Всего: <b>{total}</b>. Страница <b>{page + 1} / {pages}</b>.\n\n"
        "Нажмите на пользователя для просмотра и удаления:"
    )

    btns = []
    for row in rows:
        tid = row["telegram_id"]
        uname = (row.get("username") or "—")[:24]
        btns.append([InlineKeyboardButton(
            text=f"{tid} · {uname}",
            callback_data=f"admin:stage_users:v:{tid}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="◀️", callback_data=f"admin:stage_users:p:{page - 1}",
        ))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton(
            text="▶️", callback_data=f"admin:stage_users:p:{page + 1}",
        ))
    if nav:
        btns.append(nav)
    btns.append([InlineKeyboardButton(text="← В админку", callback_data="admin:main")])

    await safe_edit_text(
        callback.message, text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
        bot=callback.bot, parse_mode="HTML",
    )


@admin_stage_users_router.callback_query(F.data == "admin:stage_users")
async def callback_stage_users_root(callback: CallbackQuery):
    if not _is_authorized(callback):
        await callback.answer("Доступно только в STAGE для админа.", show_alert=True)
        return
    await callback.answer()
    await _render_users_page(callback, page=0)


@admin_stage_users_router.callback_query(F.data.startswith("admin:stage_users:p:"))
async def callback_stage_users_page(callback: CallbackQuery):
    if not _is_authorized(callback):
        await callback.answer("Доступно только в STAGE для админа.", show_alert=True)
        return
    await callback.answer()
    try:
        page = int(callback.data.rsplit(":", 1)[-1])
    except ValueError:
        return
    await _render_users_page(callback, page=page)


@admin_stage_users_router.callback_query(F.data.startswith("admin:stage_users:v:"))
async def callback_stage_users_view(callback: CallbackQuery):
    if not _is_authorized(callback):
        await callback.answer("Доступно только в STAGE для админа.", show_alert=True)
        return
    await callback.answer()
    try:
        tid = int(callback.data.rsplit(":", 1)[-1])
    except ValueError:
        return

    user = await database.get_user(tid)
    if not user:
        await callback.answer("Пользователь уже удалён.", show_alert=True)
        await _render_users_page(callback, page=0)
        return

    sub_info = "—"
    try:
        sub = await database.get_subscription(tid)
        if sub:
            sub_type = sub.get("subscription_type") or "basic"
            exp = sub.get("expires_at")
            exp_str = exp.strftime("%d.%m.%Y") if exp else "—"
            extras = " (bypass-only)" if sub.get("is_bypass_only") else ""
            sub_info = f"{sub_type}, до {exp_str}{extras}"
    except Exception as e:
        logger.warning(f"STAGE_USERS_VIEW: get_subscription failed user={tid}: {e}")

    created = user.get("created_at")
    created_str = created.strftime("%d.%m.%Y %H:%M") if created else "—"

    text = (
        "👤 <b>Пользователь</b>\n\n"
        f"ID: <code>{tid}</code>\n"
        f"Username: {user.get('username') or '—'}\n"
        f"Язык: {user.get('language') or '—'}\n"
        f"Создан: {created_str}\n"
        f"Подписка: {sub_info}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🗑 Удалить из БД",
            callback_data=f"admin:delete_user:{tid}",
        )],
        [InlineKeyboardButton(text="← К списку", callback_data="admin:stage_users")],
    ])
    await safe_edit_text(
        callback.message, text, reply_markup=kb,
        bot=callback.bot, parse_mode="HTML",
    )
