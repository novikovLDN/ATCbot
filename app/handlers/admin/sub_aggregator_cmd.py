"""
/aggregator — admin-only команда для тестирования sub-aggregator сервиса.

Флоу:
1. Админ пишет /aggregator в боте.
2. Хендлер вызывает sub_aggregator.ensure_pair(admin_tg_id):
   - читает main_url + gb_url из subscriptions
   - upsert в sub_pairs
   - зовёт /internal/invalidate чтобы сбросить кеш агрегатора
3. Возвращает публичный URL агрегатора для копирования в клиент.

Пока SUB_AGGREGATOR_ADMIN_ONLY=true — эта команда единственный способ
получить aggregator ссылку. Никакие пользовательские экраны (профиль,
покупка и т.п.) агрегатор не отдают.

После валидации админом — флип SUB_AGGREGATOR_ADMIN_ONLY=false и
дописать вызов sub_aggregator.ensure_pair() в места отдачи ссылки
(profile screen, purchase success, /white).
"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

import config
from app.services import sub_aggregator
from app.utils.security import admin_only

logger = logging.getLogger(__name__)

sub_aggregator_admin_router = Router()


@sub_aggregator_admin_router.message(Command("aggregator"))
@admin_only
async def cmd_aggregator(message: Message) -> None:
    """Показать/пересоздать aggregator URL для админа. Debug-команда бета-фазы."""
    tg_id = message.from_user.id
    logger.info(
        "SUB_AGGREGATOR_CMD_ENTERED tg=%s enabled=%s url=%s admin_only=%s",
        tg_id, config.SUB_AGGREGATOR_ENABLED, config.SUB_AGGREGATOR_URL,
        config.SUB_AGGREGATOR_ADMIN_ONLY,
    )

    try:
        await _run_aggregator_cmd(message, tg_id)
    except Exception as e:
        logger.exception("SUB_AGGREGATOR_CMD_ERROR tg=%s: %s", tg_id, e)
        await message.answer(
            f"❌ <b>Внутренняя ошибка</b>\n\n<code>{type(e).__name__}: {str(e)[:400]}</code>\n\n"
            "Смотри логи Railway — там traceback.",
            parse_mode="HTML",
        )


async def _run_aggregator_cmd(message: Message, tg_id: int) -> None:
    if not config.SUB_AGGREGATOR_ENABLED:
        await message.answer(
            "❌ <b>SUB_AGGREGATOR_ENABLED=false</b>\n\n"
            "Сервис-агрегатор отключён глобально. Установи в ENV:\n"
            "<code>SUB_AGGREGATOR_ENABLED=true</code>\n"
            "<code>SUB_AGGREGATOR_URL=https://sub.YOUR-DOMAIN</code>\n"
            "<code>SUB_AGGREGATOR_INTERNAL_SECRET=&lt;same as service INTERNAL_SECRET&gt;</code>\n"
            "и перезапусти бота.",
            parse_mode="HTML",
        )
        return
    if not config.SUB_AGGREGATOR_URL:
        await message.answer(
            "❌ <b>SUB_AGGREGATOR_URL пуст</b>\n\n"
            "Установи <code>SUB_AGGREGATOR_URL=https://sub.YOUR-DOMAIN</code> и перезапусти.",
            parse_mode="HTML",
        )
        return

    url = await sub_aggregator.ensure_pair(tg_id)
    if not url:
        await message.answer(
            "⚠️ <b>Не удалось создать aggregator-пару</b>\n\n"
            "Проверь что у тебя есть <b>обе</b> ссылки Remnawave в БД "
            "(premium + bypass):\n"
            "<code>SELECT remnawave_premium_sub_url, remnawave_bypass_sub_url\n"
            "FROM subscriptions WHERE telegram_id = &lt;твой tg&gt;;</code>\n\n"
            "Если одной из них нет — сначала соверши покупку/активируй "
            "trial чтобы создались обе entity в панели.",
            parse_mode="HTML",
        )
        return

    # ⚠️ Telegram запрещает custom-protocol (happ://, v2raytun://, clash://)
    # в url-кнопках inline-клавиатуры — Bad Request Unsupported URL protocol.
    # Кладём deep-link'и в текст: Telegram сам подсвечивает их clickable.
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Скопировать / открыть в браузере", url=url)],
        [InlineKeyboardButton(
            text="↻ Перевыпустить + сбросить кеш",
            callback_data="agg_admin_refresh",
        )],
    ])

    scope = "ADMIN-ONLY" if config.SUB_AGGREGATOR_ADMIN_ONLY else "ALL USERS"
    text = (
        "🔗 <b>Sub-Aggregator URL</b>\n\n"
        f"<code>{url}</code>\n\n"
        "<b>Deep-links</b> (тапни, чтобы открыть в клиенте):\n"
        f"• Happ: <code>happ://add/{url}</code>\n"
        f"• v2rayTun: <code>v2raytun://import/{url}</code>\n"
        f"• Streisand: <code>streisand://import/{url}</code>\n\n"
        f"Scope: <b>{scope}</b>\n"
        "Кэш агрегатора сброшен — следующий запрос перечитает обе апстрим ссылки.\n\n"
        "<b>Тест-план:</b>\n"
        "1. Открой ссылку в Happ / v2rayTun / Incy\n"
        "2. Клиент должен показать конфиги обоих типов (main + bypass)\n"
        "3. Убедись что <code>subscription-userinfo</code> корректный: "
        "трафик берётся из bypass, срок — из premium\n"
        "4. Отчитайся — тогда флипнем <code>SUB_AGGREGATOR_ADMIN_ONLY=false</code> "
        "и всё сообщество получит ссылку"
    )
    await message.answer(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)


@sub_aggregator_admin_router.callback_query(lambda c: c.data == "agg_admin_refresh")
@admin_only
async def cb_aggregator_refresh(callback) -> None:
    tg_id = callback.from_user.id
    url = await sub_aggregator.ensure_pair(tg_id)
    if not url:
        await callback.answer("Нет обеих ссылок в БД — не могу пересоздать", show_alert=True)
        return
    await callback.answer("Пара обновлена, кеш сброшен ✓", show_alert=True)
    logger.info("SUB_AGGREGATOR_ADMIN_REFRESH tg=%s url=%s", tg_id, url)


@sub_aggregator_admin_router.message(lambda m: m.text and m.text.strip().lower().startswith("/aggregator"))
@admin_only
async def cmd_aggregator_fallback(message: Message) -> None:
    """Fallback: если Command('aggregator') не сработал (FSM state / фильтр),
    ловим по text-startswith и логируем — понятно ли, что message доехал."""
    logger.warning(
        "SUB_AGGREGATOR_FALLBACK_HIT tg=%s text=%r — Command filter не сработал, "
        "видимо активный FSM state. Форсируем.",
        message.from_user.id, message.text,
    )
    # Очищаем state если есть — иначе Command filter продолжит игнориться.
    try:
        from aiogram.fsm.context import FSMContext  # type: ignore  # noqa: F401
    except Exception:
        pass
    await cmd_aggregator(message)


__all__ = ["sub_aggregator_admin_router"]
