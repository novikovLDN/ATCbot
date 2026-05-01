"""
Admin: bypass GB gift links.

Flow:
  /admin → 🎁 Гифт-ссылки на ГБ → ➕ Создать ссылку
    → choose validity (1/3/5/7/10/14 days)
    → choose GB amount (preset or custom)
    → choose max uses (preset or custom)
    → confirm → bot replies with t.me/<bot>?start=bgift_<CODE>

Stats: admin can list all links and see per-link redemption counts and
the list of users who redeemed each link.

Redemption flow lives in `app/handlers/user/start.py` (deep link handler).
"""
import logging
from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.utils import safe_edit_text
from app.handlers.common.states import AdminCreateBypassGiftLink
from app.handlers.admin.keyboards import (
    get_admin_dashboard_keyboard,
    get_admin_bypass_gift_menu_keyboard,
    get_admin_bypass_gift_validity_keyboard,
    get_admin_bypass_gift_gb_keyboard,
    get_admin_bypass_gift_max_uses_keyboard,
    get_admin_bypass_gift_confirm_keyboard,
    get_admin_bypass_gift_link_actions_keyboard,
    get_admin_bypass_gift_back_keyboard,
    get_admin_bypass_gift_list_keyboard,
)

admin_bypass_gift_router = Router()
logger = logging.getLogger(__name__)

# Hard caps — guard against admin typos like "10000000".
MAX_GB_PER_LINK = 10000        # 10 TB
MAX_USES_PER_LINK = 100000
LIST_PAGE_SIZE = 10


def _is_admin(user_id: int) -> bool:
    return user_id == config.ADMIN_TELEGRAM_ID


def _build_share_link(code: str) -> str:
    return f"https://t.me/{config.BOT_USERNAME}?start=bgift_{code}"


def _format_link_summary(link: dict) -> str:
    """Render a one-link summary for the admin list/detail view."""
    code = link.get("code", "?")
    gb = link.get("gb_amount", 0)
    validity = link.get("validity_days", 0)
    used = link.get("redemption_count", 0)
    total = link.get("max_uses", 0)
    expires_at = link.get("expires_at")
    deleted_at = link.get("deleted_at")
    now = datetime.now(timezone.utc)

    if deleted_at is not None:
        status = "🗑 Удалена"
    elif expires_at is not None and expires_at <= now:
        status = "⏰ Истекла"
    elif used >= total:
        status = "✅ Лимит исчерпан"
    else:
        status = "🟢 Активна"

    expires_str = expires_at.strftime("%d.%m.%Y %H:%M") if expires_at else "—"
    return (
        f"<b>Код:</b> <code>{code}</code>\n"
        f"<b>Статус:</b> {status}\n"
        f"<b>ГБ за активацию:</b> {gb}\n"
        f"<b>Использований:</b> {used} / {total}\n"
        f"<b>Срок действия:</b> {validity} дн. (до {expires_str} UTC)\n"
        f"<b>Ссылка:</b> <code>{_build_share_link(code)}</code>"
    )


# ── Entry: open the gift-link section ──────────────────────────────────

@admin_bypass_gift_router.callback_query(F.data == "admin:bgift")
async def callback_admin_bgift_menu(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    await state.clear()
    language = await resolve_user_language(callback.from_user.id)

    summary = await database.get_bypass_gift_links_summary(created_by=None)
    text = (
        "🎁 <b>Гифт-ссылки на ГБ</b>\n\n"
        "Создайте ссылку с заданным сроком действия, количеством ГБ и числом активаций. "
        "Каждый пользователь может активировать каждую ссылку только один раз.\n\n"
        f"📊 <b>Сводка:</b>\n"
        f"  • Всего ссылок: <b>{summary['total_links']}</b>\n"
        f"  • Активных: <b>{summary['active_links']}</b>\n"
        f"  • Активаций: <b>{summary['total_redemptions']}</b>\n"
        f"  • Выдано ГБ: <b>{summary['total_gb_granted']}</b>"
    )
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=get_admin_bypass_gift_menu_keyboard(language),
        parse_mode="HTML",
    )
    await callback.answer()


# ── Cancel ─────────────────────────────────────────────────────────────

@admin_bypass_gift_router.callback_query(F.data == "admin:bgift_cancel")
async def callback_admin_bgift_cancel(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    await state.clear()
    language = await resolve_user_language(callback.from_user.id)
    text = i18n_get_text(language, "admin.dashboard_title")
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=get_admin_dashboard_keyboard(language),
        parse_mode="HTML",
    )
    await callback.answer("Отменено")


# ── Step 1: choose validity ────────────────────────────────────────────

@admin_bypass_gift_router.callback_query(F.data == "admin:bgift_create")
async def callback_admin_bgift_create(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    await state.clear()
    await state.set_state(AdminCreateBypassGiftLink.waiting_for_validity)
    language = await resolve_user_language(callback.from_user.id)
    text = (
        "🎁 <b>Создание гифт-ссылки</b>\n\n"
        "Шаг 1 из 3 — <b>срок действия ссылки</b>.\n"
        "Сколько дней ссылка будет активна для активации?"
    )
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=get_admin_bypass_gift_validity_keyboard(language),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_bypass_gift_router.callback_query(
    F.data.startswith("admin:bgift_validity:"),
    AdminCreateBypassGiftLink.waiting_for_validity,
)
async def callback_admin_bgift_validity(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    try:
        days = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Неверное значение", show_alert=True)
        return
    if days not in (1, 3, 5, 7, 10, 14):
        await callback.answer("Неверный срок", show_alert=True)
        return

    await state.update_data(bgift_validity_days=days)
    await state.set_state(AdminCreateBypassGiftLink.waiting_for_gb)
    language = await resolve_user_language(callback.from_user.id)
    text = (
        f"🎁 <b>Создание гифт-ссылки</b>\n\n"
        f"✅ Срок действия: <b>{days} дн.</b>\n\n"
        f"Шаг 2 из 3 — <b>количество ГБ за активацию</b>."
    )
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=get_admin_bypass_gift_gb_keyboard(language),
        parse_mode="HTML",
    )
    await callback.answer()


# ── Step 2: GB amount (preset or custom) ───────────────────────────────

@admin_bypass_gift_router.callback_query(
    F.data.startswith("admin:bgift_gb:"),
    AdminCreateBypassGiftLink.waiting_for_gb,
)
async def callback_admin_bgift_gb(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    try:
        gb = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Неверное значение", show_alert=True)
        return
    if gb <= 0 or gb > MAX_GB_PER_LINK:
        await callback.answer("Неверное число ГБ", show_alert=True)
        return
    await _proceed_to_max_uses(callback, state, gb)


@admin_bypass_gift_router.callback_query(
    F.data == "admin:bgift_gb_custom",
    AdminCreateBypassGiftLink.waiting_for_gb,
)
async def callback_admin_bgift_gb_custom(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    await state.set_state(AdminCreateBypassGiftLink.waiting_for_gb_custom)
    text = (
        "✏️ Введите <b>количество ГБ</b> числом (1–10000).\n\n"
        "Пример: <code>15</code>"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin:bgift_cancel")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@admin_bypass_gift_router.message(AdminCreateBypassGiftLink.waiting_for_gb_custom)
async def message_admin_bgift_gb_custom(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    try:
        gb = int((message.text or "").strip())
    except ValueError:
        await message.answer(
            "❌ Нужно целое число от 1 до 10000. Попробуйте ещё раз.",
            parse_mode="HTML",
        )
        return
    if gb <= 0 or gb > MAX_GB_PER_LINK:
        await message.answer(
            f"❌ Значение должно быть от 1 до {MAX_GB_PER_LINK}. Попробуйте ещё раз.",
            parse_mode="HTML",
        )
        return
    await _proceed_to_max_uses(message, state, gb)


async def _proceed_to_max_uses(source, state: FSMContext, gb: int):
    """Helper: store gb, advance to max-uses step, send the prompt.

    `source` is either a CallbackQuery (preset GB selection) or a Message
    (custom GB entry). Both expose `from_user`.
    """
    data = await state.get_data()
    days = data.get("bgift_validity_days")
    await state.update_data(bgift_gb=gb)
    await state.set_state(AdminCreateBypassGiftLink.waiting_for_max_uses)

    language = await resolve_user_language(source.from_user.id)
    text = (
        f"🎁 <b>Создание гифт-ссылки</b>\n\n"
        f"✅ Срок действия: <b>{days} дн.</b>\n"
        f"✅ ГБ за активацию: <b>{gb} ГБ</b>\n\n"
        f"Шаг 3 из 3 — <b>максимум активаций</b>.\n"
        f"Сколько раз ссылку можно активировать (разными пользователями)?"
    )
    keyboard = get_admin_bypass_gift_max_uses_keyboard(language)
    if isinstance(source, CallbackQuery):
        await safe_edit_text(source.message, text, reply_markup=keyboard, parse_mode="HTML")
        await source.answer()
    else:
        await source.answer(text, reply_markup=keyboard, parse_mode="HTML")


# ── Step 3: max_uses (preset or custom) ────────────────────────────────

@admin_bypass_gift_router.callback_query(
    F.data.startswith("admin:bgift_uses:"),
    AdminCreateBypassGiftLink.waiting_for_max_uses,
)
async def callback_admin_bgift_uses(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    try:
        uses = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Неверное значение", show_alert=True)
        return
    if uses <= 0 or uses > MAX_USES_PER_LINK:
        await callback.answer("Неверное число активаций", show_alert=True)
        return
    await _proceed_to_confirm(callback, state, uses)


@admin_bypass_gift_router.callback_query(
    F.data == "admin:bgift_uses_custom",
    AdminCreateBypassGiftLink.waiting_for_max_uses,
)
async def callback_admin_bgift_uses_custom(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    await state.set_state(AdminCreateBypassGiftLink.waiting_for_max_uses_custom)
    text = (
        f"✏️ Введите <b>максимум активаций</b> числом (1–{MAX_USES_PER_LINK}).\n\n"
        f"Пример: <code>25</code>"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin:bgift_cancel")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@admin_bypass_gift_router.message(AdminCreateBypassGiftLink.waiting_for_max_uses_custom)
async def message_admin_bgift_uses_custom(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    try:
        uses = int((message.text or "").strip())
    except ValueError:
        await message.answer(
            f"❌ Нужно целое число от 1 до {MAX_USES_PER_LINK}. Попробуйте ещё раз.",
            parse_mode="HTML",
        )
        return
    if uses <= 0 or uses > MAX_USES_PER_LINK:
        await message.answer(
            f"❌ Значение должно быть от 1 до {MAX_USES_PER_LINK}. Попробуйте ещё раз.",
            parse_mode="HTML",
        )
        return
    await _proceed_to_confirm(message, state, uses)


async def _proceed_to_confirm(source, state: FSMContext, uses: int):
    """`source` is CallbackQuery (preset uses) or Message (custom uses)."""
    data = await state.get_data()
    days = data.get("bgift_validity_days")
    gb = data.get("bgift_gb")
    await state.update_data(bgift_max_uses=uses)
    await state.set_state(AdminCreateBypassGiftLink.waiting_for_confirm)

    language = await resolve_user_language(source.from_user.id)
    text = (
        f"🎁 <b>Подтверждение создания</b>\n\n"
        f"⏳ Срок действия ссылки: <b>{days} дн.</b>\n"
        f"📦 ГБ за активацию: <b>{gb} ГБ</b>\n"
        f"👥 Максимум активаций: <b>{uses}</b>\n\n"
        f"Каждый пользователь может активировать ссылку только 1 раз. "
        f"При активации в Remnawave автоматически добавится {gb} ГБ обхода. "
        f"Если у пользователя ещё нет аккаунта Remnawave — он будет создан."
    )
    keyboard = get_admin_bypass_gift_confirm_keyboard(language)
    if isinstance(source, CallbackQuery):
        await safe_edit_text(source.message, text, reply_markup=keyboard, parse_mode="HTML")
        await source.answer()
    else:
        await source.answer(text, reply_markup=keyboard, parse_mode="HTML")


# ── Confirm: create the link ──────────────────────────────────────────

@admin_bypass_gift_router.callback_query(
    F.data == "admin:bgift_confirm",
    AdminCreateBypassGiftLink.waiting_for_confirm,
)
async def callback_admin_bgift_confirm(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    data = await state.get_data()
    days = data.get("bgift_validity_days")
    gb = data.get("bgift_gb")
    uses = data.get("bgift_max_uses")
    if not days or not gb or not uses:
        await callback.answer("Неполные данные, начните заново", show_alert=True)
        await state.clear()
        return

    link = await database.create_bypass_gift_link(
        created_by=callback.from_user.id,
        gb_amount=gb,
        validity_days=days,
        max_uses=uses,
    )
    await state.clear()

    language = await resolve_user_language(callback.from_user.id)

    if not link:
        text = "❌ Не удалось создать ссылку. Проверьте логи."
        await safe_edit_text(
            callback.message,
            text,
            reply_markup=get_admin_bypass_gift_back_keyboard(language),
            parse_mode="HTML",
        )
        await callback.answer("Ошибка", show_alert=True)
        return

    code = link["code"]
    share_url = _build_share_link(code)
    expires_str = link["expires_at"].strftime("%d.%m.%Y %H:%M")
    text = (
        f"✅ <b>Гифт-ссылка создана</b>\n\n"
        f"<b>Код:</b> <code>{code}</code>\n"
        f"<b>ГБ за активацию:</b> {gb} ГБ\n"
        f"<b>Срок действия:</b> {days} дн. (до {expires_str} UTC)\n"
        f"<b>Максимум активаций:</b> {uses}\n\n"
        f"<b>Ссылка для отправки:</b>\n<code>{share_url}</code>\n\n"
        f"Каждый пользователь сможет активировать её только 1 раз. "
        f"При повторном переходе бот напомнит, что ссылка уже активирована."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data=f"admin:bgift_view:{link['id']}")],
        [InlineKeyboardButton(text="📋 Все ссылки", callback_data="admin:bgift_list:0")],
        [InlineKeyboardButton(text="⬅️ В раздел", callback_data="admin:bgift")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer("Создано")
    logger.info(
        "BGIFT_LINK_CREATED admin=%s code=%s gb=%s validity=%sd uses=%s",
        callback.from_user.id, code, gb, days, uses,
    )


# ── List / view / delete ──────────────────────────────────────────────

@admin_bypass_gift_router.callback_query(F.data.startswith("admin:bgift_list:"))
async def callback_admin_bgift_list(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    try:
        page = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        page = 0
    page = max(0, page)
    language = await resolve_user_language(callback.from_user.id)

    # Fetch one extra row to detect "has next page".
    fetched = await database.list_bypass_gift_links(
        created_by=None,
        include_deleted=True,
        limit=LIST_PAGE_SIZE + 1,
        offset=page * LIST_PAGE_SIZE,
    )
    has_next = len(fetched) > LIST_PAGE_SIZE
    links = fetched[:LIST_PAGE_SIZE]

    if not links and page == 0:
        text = (
            "🎁 <b>Гифт-ссылки на ГБ</b>\n\n"
            "Пока ни одной ссылки не создано."
        )
    elif not links:
        text = "🎁 <b>Гифт-ссылки на ГБ</b>\n\nНа этой странице пусто."
    else:
        lines = ["🎁 <b>Гифт-ссылки на ГБ</b>", ""]
        now = datetime.now(timezone.utc)
        for link in links:
            code = link.get("code", "?")
            gb = link.get("gb_amount", 0)
            used = link.get("redemption_count", 0)
            total = link.get("max_uses", 0)
            expires = link.get("expires_at")
            deleted = link.get("deleted_at") is not None
            if deleted:
                status = "🗑"
            elif expires is not None and expires <= now:
                status = "⏰"
            elif used >= total:
                status = "✅"
            else:
                status = "🟢"
            lines.append(f"{status} <code>{code}</code> · {gb} ГБ · {used}/{total}")
        text = "\n".join(lines)

    keyboard = get_admin_bypass_gift_list_keyboard(links, page, has_next, language)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@admin_bypass_gift_router.callback_query(F.data.startswith("admin:bgift_view:"))
async def callback_admin_bgift_view(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    try:
        link_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Неверный ID", show_alert=True)
        return
    language = await resolve_user_language(callback.from_user.id)

    link = await database.get_bypass_gift_link_by_id(link_id)
    if not link:
        await callback.answer("Ссылка не найдена", show_alert=True)
        return

    redemptions = await database.get_bypass_gift_link_redemptions(link_id, limit=20)
    link["redemption_count"] = await database.count_bypass_gift_link_redemptions(link_id)

    text = "🎁 <b>Информация о гифт-ссылке</b>\n\n" + _format_link_summary(link)
    if redemptions:
        text += "\n\n<b>Последние активации:</b>"
        for r in redemptions[:10]:
            ts = r.get("redeemed_at")
            ts_str = ts.strftime("%d.%m.%Y %H:%M") if ts else "—"
            text += f"\n• <code>{r['telegram_id']}</code> — {r['gb_granted']} ГБ — {ts_str}"
        if len(redemptions) > 10:
            text += f"\n…и ещё {len(redemptions) - 10}"
    else:
        text += "\n\n<i>Пока никто не активировал.</i>"

    await safe_edit_text(
        callback.message,
        text,
        reply_markup=get_admin_bypass_gift_link_actions_keyboard(link_id, language),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_bypass_gift_router.callback_query(F.data.startswith("admin:bgift_redemptions:"))
async def callback_admin_bgift_redemptions(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    try:
        link_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Неверный ID", show_alert=True)
        return
    language = await resolve_user_language(callback.from_user.id)

    link = await database.get_bypass_gift_link_by_id(link_id)
    if not link:
        await callback.answer("Ссылка не найдена", show_alert=True)
        return
    redemptions = await database.get_bypass_gift_link_redemptions(link_id, limit=200)

    text = (
        f"📊 <b>Активации ссылки <code>{link['code']}</code></b>\n\n"
        f"Всего активаций: <b>{len(redemptions)}</b> / {link['max_uses']}\n"
    )
    if redemptions:
        text += "\n"
        for r in redemptions[:50]:
            ts = r.get("redeemed_at")
            ts_str = ts.strftime("%d.%m.%Y %H:%M") if ts else "—"
            text += f"• <code>{r['telegram_id']}</code> · {r['gb_granted']} ГБ · {ts_str}\n"
        if len(redemptions) > 50:
            text += f"\n…и ещё {len(redemptions) - 50}"
    else:
        text += "\n<i>Пока никто не активировал.</i>"

    await safe_edit_text(
        callback.message,
        text,
        reply_markup=get_admin_bypass_gift_link_actions_keyboard(link_id, language),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_bypass_gift_router.callback_query(F.data.startswith("admin:bgift_delete:"))
async def callback_admin_bgift_delete(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    try:
        link_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Неверный ID", show_alert=True)
        return
    language = await resolve_user_language(callback.from_user.id)

    ok = await database.soft_delete_bypass_gift_link(link_id)
    if ok:
        text = (
            "🗑 <b>Ссылка удалена.</b>\n\n"
            "Существующие активации сохранены, новые переходы по ссылке "
            "будут отклонены с пометкой «удалена»."
        )
        await callback.answer("Удалено")
        logger.info("BGIFT_LINK_DELETED admin=%s link_id=%s", callback.from_user.id, link_id)
    else:
        text = "❌ Ссылка не найдена или уже удалена."
        await callback.answer("Не найдено", show_alert=True)

    await safe_edit_text(
        callback.message,
        text,
        reply_markup=get_admin_bypass_gift_back_keyboard(language),
        parse_mode="HTML",
    )
