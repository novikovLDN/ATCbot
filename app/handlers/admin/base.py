"""
Bot-side admin: the /admin entry (web dashboard link, «Написать пользователю»,
dashboard password reset), the admin → user chat and /platega_sub_status.

The rest of the old in-bot admin panel was removed (owner decision
2026-09-14): everything else lives in the web dashboard. The shop delivery
handlers (apple_id_delivery.py, spotify_delivery.py) are separate modules.
"""
import logging

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command

import config
import database
from app.utils.security import admin_only
from app.handlers.common.utils import safe_edit_text
from app.handlers.common.states import AdminChat

admin_base_router = Router()
logger = logging.getLogger(__name__)


async def _build_admin_menu(message_or_callback) -> tuple[str, InlineKeyboardMarkup]:
    """Build the bot-side admin entry message: open dashboard, write to a
    user, reset password. Everything else is in the web dashboard."""
    from app.api.dashboard.auth import issue_login_token
    from app.services import admin_auth

    enabled = getattr(config, "DASHBOARD_ENABLED", False)
    has_password = False
    try:
        has_password = await admin_auth.credentials_exist()
    except Exception:
        pass

    rows: list[list[InlineKeyboardButton]] = []
    if enabled:
        try:
            token = issue_login_token(_admin_id(message_or_callback))
            url = f"{config.DASHBOARD_BASE_URL.rstrip('/')}/dashboard/?login={token}"
            rows.append([InlineKeyboardButton(text="🛡 Открыть дашборд", url=url)])
        except Exception as e:
            logger.warning("DASHBOARD_LINK_FAIL: %s", e)

    rows.append([InlineKeyboardButton(text="💬 Написать пользователю", callback_data="admin:chat")])
    rows.append([InlineKeyboardButton(
        text="🔄 Сбросить пароль" if has_password else "🆕 Установить пароль",
        callback_data="admin:reset_password",
    )])

    if has_password:
        body = (
            "🛡 <b>Atlas Admin</b>\n\n"
            "Открой дашборд — войдёшь по уже установленному логину и паролю.\n\n"
            "Если забыл пароль — жми <b>«Сбросить пароль»</b>, "
            "потом снова открой дашборд и придумай новый."
        )
    else:
        body = (
            "🛡 <b>Atlas Admin</b>\n\n"
            "Это твой первый вход. Нажми <b>«Открыть дашборд»</b> — там "
            "тебя попросят придумать логин и пароль. "
            "После этого ссылка перестанет автоматически впускать "
            "в дашборд; для входа понадобятся логин/пароль."
        )
    return body, InlineKeyboardMarkup(inline_keyboard=rows)


def _admin_id(obj) -> int:
    if hasattr(obj, "from_user") and obj.from_user is not None:
        return int(obj.from_user.id)
    return int(config.ADMIN_TELEGRAM_ID)


@admin_base_router.message(Command("admin"))
@admin_only
async def cmd_admin(message: Message):
    """Bot-side admin entry: dashboard magic-link, «Написать пользователю»,
    password reset. The full admin panel is the web dashboard."""
    body, kb = await _build_admin_menu(message)
    await message.answer(body, reply_markup=kb, parse_mode="HTML")


@admin_base_router.callback_query(F.data == "admin:main")
@admin_only
async def callback_admin_main(callback: CallbackQuery, state: FSMContext):
    """«Отмена» / «← Админ-панель» in the chat flow (and buttons of older admin
    messages) → back to the /admin menu. Leaves the chat flow if it was open."""
    try:
        await callback.answer()
    except Exception:
        pass
    if await state.get_state() in (AdminChat.waiting_for_user_id.state, AdminChat.chatting.state):
        await state.clear()
    body, kb = await _build_admin_menu(callback)
    try:
        await callback.message.edit_text(body, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(body, reply_markup=kb, parse_mode="HTML")


@admin_base_router.message(Command("platega_sub_status"))
@admin_only
async def cmd_platega_sub_status(message: Message):
    """Диагностика Platega после отключения рекуррентных подписок: подхватились
    ли merchant_id/secret и какие подписки из беты ещё живы в БД (их надо
    отменить в кабинете Platega)."""
    lines = ["🔧 <b>Platega subscription status</b>", ""]
    try:
        import platega_service
    except Exception as e:
        await message.answer(
            f"❌ Импорт platega_service упал: <code>{e}</code>", parse_mode="HTML",
        )
        return

    mid = platega_service.PLATEGA_MERCHANT_ID or ""
    sec = platega_service.PLATEGA_SECRET or ""
    lines.append(f"• MERCHANT_ID loaded: <b>{'YES' if mid else 'NO'}</b> (длина {len(mid)})")
    lines.append(f"• SECRET loaded: <b>{'YES' if sec else 'NO'}</b> (длина {len(sec)})")
    lines.append(f"• API URL: <code>{platega_service.PLATEGA_API_URL}</code>")
    lines.append(f"• is_enabled(): <b>{platega_service.is_enabled()}</b>")
    lines.append("")
    lines.append(
        "⛔ <b>Рекуррентные подписки отключены.</b> Callback'и по ним "
        "(<code>/webhooks/platega-subscription</code> и общий URL) только "
        "шлют алерт — доступ не выдаётся."
    )
    lines.append("")

    # DB — подписки из беты, которые Platega ещё может списывать.
    try:
        from database import platega_subscriptions as _psub_db
        subs = await _psub_db.list_live_subscriptions(limit=20)
        lines.append(f"• Живых подписок в БД (Active/PendingAgreement/PastDue): <b>{len(subs)}</b>")
        for s in subs[:20]:
            lines.append(
                f"   — <code>{s['subscription_id']}</code> tg={s['telegram_id']} "
                f"status={s['status']} next={s.get('next_charge_at') or '-'}"
            )
        if subs:
            lines.append("<i>Отменить в кабинете Platega: POST /subscription/{id}/cancel</i>")
    except Exception as e:
        lines.append(f"• DB probe: <b>FAIL</b> — {type(e).__name__}: {str(e)[:80]}")

    await message.answer("\n".join(lines), parse_mode="HTML")


@admin_base_router.callback_query(F.data == "admin:reset_password")
@admin_only
async def callback_reset_password(callback: CallbackQuery):
    """Confirm-then-clear admin web credentials + every active
    session. Next dashboard visit will ask the admin to set new
    login/password."""
    try:
        await callback.answer()
    except Exception:
        pass
    rows = [
        [InlineKeyboardButton(
            text="⚠️ Да, сбросить",
            callback_data="admin:reset_password_confirm",
        )],
        [InlineKeyboardButton(
            text="❌ Отмена", callback_data="admin:reset_password_cancel",
        )],
    ]
    text = (
        "⚠️ <b>Сбросить пароль?</b>\n\n"
        "Будет удалён логин/пароль и все активные сессии. "
        "При следующем открытии дашборда ты заново придумаешь "
        "логин и пароль через magic-ссылку.\n\n"
        "Старые открытые вкладки/PWA на устройствах разлогинятся."
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML",
        )


@admin_base_router.callback_query(F.data == "admin:reset_password_cancel")
@admin_only
async def callback_reset_password_cancel(callback: CallbackQuery):
    try:
        await callback.answer("Отменено")
    except Exception:
        pass
    body, kb = await _build_admin_menu(callback)
    try:
        await callback.message.edit_text(body, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(body, reply_markup=kb, parse_mode="HTML")


@admin_base_router.callback_query(F.data == "admin:reset_password_confirm")
@admin_only
async def callback_reset_password_confirm(callback: CallbackQuery):
    from app.services import admin_auth
    try:
        await callback.answer()
    except Exception:
        pass

    try:
        ok = await admin_auth.clear_credentials()
    except Exception as e:
        logger.exception("reset_password_confirm clear_credentials error: %s", e)
        ok = False

    if not ok:
        try:
            await callback.message.answer("❌ Не удалось сбросить. Попробуй ещё раз.")
        except Exception:
            pass
        return

    body, kb = await _build_admin_menu(callback)
    final_body = (
        "✅ <b>Сброшено</b>\n\n"
        "Логин и пароль удалены, все сессии закрыты.\n\n"
        f"{body}"
    )
    try:
        await callback.message.edit_text(
            final_body, reply_markup=kb, parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(
            final_body, reply_markup=kb, parse_mode="HTML",
        )


# ── Admin Chat (send message to user) ───────────────────────────

@admin_base_router.callback_query(F.data == "admin:chat")
async def callback_admin_chat_start(callback: CallbackQuery, state: FSMContext):
    """Start admin chat — ask for user ID."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("⛔️", show_alert=True)
        return
    await callback.answer()
    await state.set_state(AdminChat.waiting_for_user_id)
    await safe_edit_text(
        callback.message,
        "💬 <b>Написать пользователю</b>\n\n"
        "Введите Telegram ID или @username пользователя:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin:main")],
        ]),
        bot=callback.bot,
    )


@admin_base_router.message(AdminChat.waiting_for_user_id)
async def process_admin_chat_user_id(message: Message, state: FSMContext):
    """Process user ID input, enter chatting mode."""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    if message.text and message.text.strip().lower() in ("/cancel", "отмена"):
        await state.clear()
        await message.answer("Отменено.", parse_mode="HTML")
        return

    user_input = message.text.strip() if message.text else ""

    # Find user by ID or username
    target_user_id = None
    target_username = None
    try:
        target_user_id = int(user_input)
    except ValueError:
        # Try username
        username = user_input.lstrip("@").lower()
        if username:
            pool = await database.get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT telegram_id, username FROM users WHERE LOWER(username) = $1",
                    username,
                )
            if row:
                target_user_id = row["telegram_id"]
                target_username = row["username"]

    if not target_user_id:
        await message.answer("❌ Пользователь не найден. Введите корректный ID или @username:", parse_mode="HTML")
        return

    if not target_username:
        user = await database.get_user(target_user_id)
        target_username = user.get("username") if user else None

    uname_display = f"@{target_username}" if target_username else str(target_user_id)

    await state.update_data(chat_target_id=target_user_id, chat_target_name=uname_display)
    await state.set_state(AdminChat.chatting)
    await message.answer(
        f"💬 <b>Чат с {uname_display}</b> (<code>{target_user_id}</code>)\n\n"
        f"Отправляйте сообщения — бот перешлёт их пользователю.\n"
        f"Поддерживается: текст, фото, документы, стикеры.\n\n"
        f"Для завершения отправьте <code>/end</code>",
        parse_mode="HTML",
    )


@admin_base_router.message(AdminChat.chatting)
async def process_admin_chat_message(message: Message, state: FSMContext, bot: Bot):
    """Forward admin message to target user."""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return

    # Exit command
    if message.text and message.text.strip().lower() in ("/end", "/cancel", "/stop", "отмена"):
        data = await state.get_data()
        name = data.get("chat_target_name", "")
        await state.clear()
        await message.answer(
            f"✅ Чат с {name} завершён.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="← Админ-панель", callback_data="admin:main")],
            ]),
            parse_mode="HTML",
        )
        return

    data = await state.get_data()
    target_id = data.get("chat_target_id")
    target_name = data.get("chat_target_name", "")

    if not target_id:
        await state.clear()
        await message.answer("❌ Ошибка: ID пользователя потерян. Начните заново.", parse_mode="HTML")
        return

    try:
        # Forward different content types
        if message.photo:
            await bot.send_photo(
                chat_id=target_id,
                photo=message.photo[-1].file_id,
                # html_caption: escaped + keeps the admin's formatting entities
                caption=message.html_caption if message.caption else None,
                parse_mode="HTML" if message.caption else None,
            )
        elif message.document:
            await bot.send_document(
                chat_id=target_id,
                document=message.document.file_id,
                caption=message.caption or None,
            )
        elif message.sticker:
            await bot.send_sticker(chat_id=target_id, sticker=message.sticker.file_id)
        elif message.text:
            await bot.send_message(chat_id=target_id, text=message.html_text, parse_mode="HTML")
        else:
            await message.answer("⚠️ Этот тип сообщения не поддерживается.", parse_mode="HTML")
            return

        await message.answer(f"✅ Доставлено → {target_name}", parse_mode="HTML")

    except Exception as e:
        logger.warning("ADMIN_CHAT_SEND_ERROR: target=%s error=%s", target_id, e)
        await message.answer(f"❌ Не удалось отправить: {e}", parse_mode="HTML")
