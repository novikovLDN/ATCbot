"""Экран «сколько трафика осталось».

ЧТО ЗДЕСЬ
    Единственный экран, который ходит в Remnawave за реальным расходом:
    колбэк traffic_info / traffic_refresh и его близнец для команды /white,
    отвечающий новым сообщением.

ПОЧЕМУ ОТДЕЛЬНО
    Ни цен, ни выставления счетов — только чтение состояния из панели. Это
    единственная часть раздела, которую роняет недоступность Remnawave, и
    смотреть её удобно отдельно от денег.

ЧТО ЛЕГКО СЛОМАТЬ
    Две функции — почти близнецы, но НЕ дубликат: колбэк редактирует
    существующее сообщение (safe_edit_text), а версия для команды отправляет
    новое (message.answer). Свести их в одну, не разделив способ доставки,
    значит сломать один из двух путей.

    Ссылка на подписку из ответа Remnawave никогда не показывается сырой —
    happ_crypto.format_for_user заворачивает её в deeplink. Показать
    traffic["subscriptionUrl"] напрямую значит отдать пользователю
    незапечатанный ключ.
"""
import config
import database
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.services import remnawave_api, remnawave_service
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.utils import safe_edit_text
from ._shared import _format_bytes, _progress_bar

usage_router = Router()


@usage_router.callback_query(F.data.in_({"traffic_info", "traffic_refresh"}))
async def callback_traffic_info(callback: CallbackQuery):
    """Show traffic usage screen."""
    if not await ensure_db_ready_callback(callback):
        return
    await callback.answer()

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # Check active subscription
    subscription = await database.get_subscription(telegram_id)
    if not subscription:
        text = i18n_get_text(language, "traffic.no_subscription")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "traffic.buy_subscription"),
                callback_data="menu_buy_vpn",
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_main",
            )],
        ])
        await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot)
        return

    sub_type = (subscription.get("subscription_type") or "basic").strip().lower()
    is_trial = sub_type == "trial"

    rmn_uuid = await database.get_remnawave_uuid(telegram_id)
    if not rmn_uuid:
        # Auto-provision in background, show "provisioning" screen
        expires_at = subscription.get("expires_at")
        if expires_at and config.REMNAWAVE_ENABLED:
            override = 5 * 1024**3 if is_trial else 10 * 1024**3
            remnawave_service._fire_and_forget(
                remnawave_service.create_remnawave_user(
                    telegram_id, sub_type, expires_at,
                    traffic_limit_override=override,
                )
            )
            text = i18n_get_text(language, "traffic.bypass_provisioning")
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="traffic_refresh")],
                [InlineKeyboardButton(
                    text=i18n_get_text(language, "common.back"),
                    callback_data="menu_main",
                )],
            ])
            await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot)
            return
    else:
        # Ensure squad is assigned for existing users (fire-and-forget)
        remnawave_service._fire_and_forget(
            remnawave_service.ensure_squad(telegram_id)
        )
    if not rmn_uuid:
        text = i18n_get_text(language, "traffic.not_provisioned")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_main",
            )],
        ])
        await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot)
        return

    # Fetch traffic from Remnawave
    traffic = await remnawave_api.get_user_traffic(rmn_uuid)
    if not traffic:
        text = i18n_get_text(language, "traffic.fetch_error")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄", callback_data="traffic_refresh")],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_main",
            )],
        ])
        await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot)
        return

    used = traffic["usedTrafficBytes"]
    limit = traffic["trafficLimitBytes"]

    remaining = max(0, limit - used)
    pct = int(used / limit * 100) if limit > 0 else 0

    expires_at = subscription.get("expires_at")
    expires_str = expires_at.strftime("%d.%m.%Y") if expires_at else "—"

    bar = _progress_bar(used, limit)
    warning = ""
    if remaining <= 500 * 1024**2:
        warning += "\n\n❗️ " + i18n_get_text(language, "traffic.warning_critical")
    elif remaining <= 3 * 1024**3:
        warning += "\n\n⚠️ " + i18n_get_text(language, "traffic.warning_low", remaining=_format_bytes(remaining))

    # Subscription URL comes directly from Remnawave API response.
    # User should never see the raw https://sub.atlassecure.ru/... — wrap
    # it into a Happ crypt4 deeplink so the only visible key is sealed.
    from app.services import happ_crypto
    sub_url = happ_crypto.format_for_user(traffic.get("subscriptionUrl", ""))

    text = i18n_get_text(
        language,
        "traffic.info",
        used=_format_bytes(used),
        limit=_format_bytes(limit),
        bar=bar,
        pct=pct,
        expires=expires_str,
        sub_url=sub_url,
    ) + warning

    if is_trial:
        text += "\n\n💎 " + i18n_get_text(language, "traffic.trial_upgrade_hint")

    buttons = []
    if is_trial:
        from app.handlers.common.keyboards import _strip_lead_emoji
        buttons.append([InlineKeyboardButton(
            text=_strip_lead_emoji(i18n_get_text(language, "traffic.buy_subscription")),
            callback_data="menu_buy_vpn",
            icon_custom_emoji_id="5199785165735367039",  # ⚡️
        )])
    else:
        from app.handlers.common.keyboards import _strip_lead_emoji
        buttons.append([InlineKeyboardButton(
            text=_strip_lead_emoji(i18n_get_text(language, "traffic.buy_traffic_btn")),
            callback_data="buy_traffic",
            icon_custom_emoji_id="5199785165735367039",  # ⚡️
        )])
    buttons.append([InlineKeyboardButton(text="🔄", callback_data="traffic_refresh")])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="menu_main",
    )])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot, parse_mode="HTML")


async def show_traffic_info_message(message):
    """Show traffic info as a new message (for /white command)."""
    telegram_id = message.from_user.id
    language = await resolve_user_language(telegram_id)

    subscription = await database.get_subscription(telegram_id)
    if not subscription:
        text = i18n_get_text(language, "traffic.no_subscription")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "traffic.buy_subscription"),
                callback_data="menu_buy_vpn",
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_main",
            )],
        ])
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
        return

    sub_type = (subscription.get("subscription_type") or "basic").strip().lower()
    is_trial = sub_type == "trial"

    rmn_uuid = await database.get_remnawave_uuid(telegram_id)
    if not rmn_uuid:
        expires_at = subscription.get("expires_at")
        if expires_at and config.REMNAWAVE_ENABLED:
            override = 5 * 1024**3 if is_trial else 10 * 1024**3
            remnawave_service._fire_and_forget(
                remnawave_service.create_remnawave_user(
                    telegram_id, sub_type, expires_at,
                    traffic_limit_override=override,
                )
            )
            text = i18n_get_text(language, "traffic.bypass_provisioning")
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="traffic_refresh")],
                [InlineKeyboardButton(
                    text=i18n_get_text(language, "common.back"),
                    callback_data="menu_main",
                )],
            ])
            await message.answer(text, reply_markup=kb, parse_mode="HTML")
            return
        text = i18n_get_text(language, "traffic.not_provisioned")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_main",
            )],
        ])
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
        return
    else:
        remnawave_service._fire_and_forget(
            remnawave_service.ensure_squad(telegram_id)
        )

    traffic = await remnawave_api.get_user_traffic(rmn_uuid)
    if not traffic:
        text = i18n_get_text(language, "traffic.fetch_error")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄", callback_data="traffic_refresh")],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_main",
            )],
        ])
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
        return

    used = traffic["usedTrafficBytes"]
    limit = traffic["trafficLimitBytes"]
    remaining = max(0, limit - used)
    pct = int(used / limit * 100) if limit > 0 else 0
    expires_at = subscription.get("expires_at")
    expires_str = expires_at.strftime("%d.%m.%Y") if expires_at else "—"
    bar = _progress_bar(used, limit)
    warning = ""
    if remaining <= 500 * 1024**2:
        warning += "\n\n❗️ " + i18n_get_text(language, "traffic.warning_critical")
    elif remaining <= 3 * 1024**3:
        warning += "\n\n⚠️ " + i18n_get_text(language, "traffic.warning_low", remaining=_format_bytes(remaining))

    from app.services import happ_crypto
    sub_url = happ_crypto.format_for_user(traffic.get("subscriptionUrl", ""))
    text = i18n_get_text(
        language, "traffic.info",
        used=_format_bytes(used), limit=_format_bytes(limit),
        bar=bar, pct=pct, expires=expires_str, sub_url=sub_url,
    ) + warning

    if is_trial:
        text += "\n\n💎 " + i18n_get_text(language, "traffic.trial_upgrade_hint")

    buttons = []
    if is_trial:
        from app.handlers.common.keyboards import _strip_lead_emoji
        buttons.append([InlineKeyboardButton(
            text=_strip_lead_emoji(i18n_get_text(language, "traffic.buy_subscription")),
            callback_data="menu_buy_vpn",
            icon_custom_emoji_id="5199785165735367039",  # ⚡️
        )])
    else:
        from app.handlers.common.keyboards import _strip_lead_emoji
        buttons.append([InlineKeyboardButton(
            text=_strip_lead_emoji(i18n_get_text(language, "traffic.buy_traffic_btn")),
            callback_data="buy_traffic",
            icon_custom_emoji_id="5199785165735367039",  # ⚡️
        )])
    buttons.append([InlineKeyboardButton(text="🔄", callback_data="traffic_refresh")])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="menu_main",
    )])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")
