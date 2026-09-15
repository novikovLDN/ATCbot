"""
User-facing buttons of broadcasts sent from the web dashboard
(app/api/dashboard/routes/broadcasts.py::_build_reply_markup): discount /
gift offers (broadcast_promo_buy:, broadcast_gift_combo:, broadcast_gift_1m,
bcg1m:*, broadcast_gift_3m, bcg3m:*, broadcast_gift_1y_40, bcg1y40:*,
broadcast_gift_reveal:), promo traffic packs (broadcast_promo_traffic[_ext]:)
and broadcast_back_to_tariffs.

Moved verbatim from app/handlers/admin/broadcast.py when the old in-bot
admin panel (and its broadcast wizard) was removed (owner 2026-09-14).
"""
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language


broadcast_offers_router = Router()


logger = logging.getLogger(__name__)


@broadcast_offers_router.callback_query(F.data.startswith("broadcast_promo_buy:"))
async def callback_broadcast_promo_buy(callback: CallbackQuery, state: FSMContext):
    """Пользователь нажал 'Купить со скидкой' в уведомлении — автоматически применяем скидку"""
    await callback.answer()

    try:
        broadcast_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка", show_alert=True)
        return

    telegram_id = callback.from_user.id

    try:
        # Get discount from DB
        discount = await database.get_broadcast_discount(broadcast_id)
        if not discount:
            # No discount found, just redirect to tariff selection.
            # force_new_message=True — сохраняем оригинал рассылки в чате,
            # экран тарифов уходит свежим сообщением сверху.
            from app.handlers.common.screens import show_tariffs_main_screen
            await show_tariffs_main_screen(callback, state, force_new_message=True)
            return

        discount_percent = discount.get("discount_percent", 0)
        discount_hours = discount.get("discount_hours", 168)  # default 7 days
        discount_label = discount.get("discount_label", "7 дней")

        # Auto-apply discount to user with configured duration
        from datetime import timedelta
        expires_at = datetime.now(timezone.utc) + timedelta(hours=discount_hours)
        await database.create_user_discount(
            telegram_id=telegram_id,
            discount_percent=discount_percent,
            expires_at=expires_at,
            created_by=config.ADMIN_TELEGRAM_ID,
            keep_max=True,  # never lower a bigger active discount
        )

        # Redirect to tariff screen. force_new_message=True — рассылка
        # остаётся (юзер видит, на какой именно акции кликнул).
        from app.handlers.common.screens import show_tariffs_main_screen
        await show_tariffs_main_screen(callback, state, force_new_message=True)

        language = await resolve_user_language(telegram_id)
        await callback.message.answer(
            f"🎁 Скидка {discount_percent}% автоматически применена! Действует {discount_label}.",
            parse_mode="HTML",
        )

    except Exception as e:
        logger.exception(f"Error applying broadcast promo discount: {e}")
        await callback.answer("Произошла ошибка, попробуйте позже", show_alert=True)


# === Broadcast gift buttons (owner 2026-09-15) ===
# A gift button turns on a discount for N hours (a bigger active one stays)
# and opens the tariff screen — the user buys now or later there:
#   * «Забрать подарок»: the broadcast's percent on any plan and period
#     (the general personal discount, user_discounts);
#   * «−30% на 1 месяц» / «−30% на 3 месяца» / «−40% на 1 год»: ONLY that
#     period, any plan of it (user_period_discounts, migration 095) — «if the
#     button says 1 year −40 %, it is 1 year −40 %».
# The price comes from the regular purchase
# flow (calculate_price), the same one the Combo price guard uses. The old
# buttons put their own price into the purchase FSM (gift1m / gift3m /
# gift1y40 menus, gift_combo); since 2026-09-14 the guard refused those Combo
# prices (prod 2026-09-15: COMBO_PRICE_BELOW_COMBO user=502708). Bypass GB
# packs have their own discount (user_traffic_discounts) and are not affected.
# The old inner buttons (bcg1m:* / bcg3m:* / bcg1y40:*) of sent broadcasts do
# the same as their gift button.

_GIFT_HOURS = 24
# Period gift buttons: (percent, the ONLY period it discounts).
_GIFT1M = (30, 30)
_GIFT3M = (30, 90)
_GIFT1Y40 = (40, 365)

# «🎁 1 год со скидкой 40%»: the 🏆 reveal before the tariff screen (premium
# emoji; clients without Telegram Premium see a plain 🏆).
_GIFT1Y40_REVEAL_EMOJI = '<tg-emoji emoji-id="5413566144986503832">🏆</tg-emoji>'
_GIFT1Y40_REVEAL_PAUSE_SECONDS = 2.0


async def _answer(callback: CallbackQuery) -> None:
    try:
        await callback.answer()
    except Exception:
        pass


async def _show_tariffs(callback: CallbackQuery, state: FSMContext, text: str) -> None:
    """The discount message, then the tariff screen (prices from the regular flow)."""
    chat_id = callback.message.chat.id if callback.message and callback.message.chat else callback.from_user.id
    try:
        await callback.bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception as e:
        logger.warning("BROADCAST_GIFT_MSG_FAIL user=%s: %s", callback.from_user.id, e)
    # from_broadcast: «Назад» from the period screen returns to the tariff screen.
    await state.update_data(from_broadcast=True)
    from app.handlers.common.screens import show_tariffs_main_screen
    await show_tariffs_main_screen(callback, state, force_new_message=True)


async def _grant_period_discount(
    callback: CallbackQuery, state: FSMContext, *, offer: tuple, source: str,
) -> None:
    """Discount `percent` on `period_days` ONLY, for _GIFT_HOURS (a bigger active
    one on that period stays), then the tariff screen."""
    percent, period_days = offer
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    try:
        await database.create_period_discount(
            telegram_id, period_days, percent,
            datetime.now(timezone.utc) + timedelta(hours=_GIFT_HOURS), source,
        )
        period = await database.get_period_discount(telegram_id, period_days)
        general = await database.get_user_discount(telegram_id)
    except Exception as e:
        logger.exception("BROADCAST_GIFT_PERIOD_FAIL user=%s source=%s: %s", telegram_id, source, e)
        try:
            await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        except Exception:
            pass
        return

    # What the user actually gets on this period: the largest one wins.
    kept = max(int((period or {}).get("discount_percent") or 0), int((general or {}).get("discount_percent") or 0))
    if kept > percent:
        text = i18n_get_text(language, "main.discount_bigger_kept", percent=kept)
    else:
        text = i18n_get_text(
            language, "broadcast.period_discount_applied", percent=percent, hours=_GIFT_HOURS,
            period=i18n_get_text(language, f"broadcast.period_{period_days}"),
        )
    await _show_tariffs(callback, state, text)
    logger.info(
        "BROADCAST_GIFT_PERIOD_DISCOUNT user=%s source=%s pct=%s period=%s kept=%s",
        telegram_id, source, percent, period_days, kept,
    )


async def _grant_gift_discount(
    callback: CallbackQuery, state: FSMContext, *, percent: int, hours: int, source: str,
) -> None:
    """Personal discount `percent` for `hours` (keep_max), then the tariff screen."""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    try:
        await database.create_user_discount(
            telegram_id=telegram_id,
            discount_percent=percent,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=hours),
            created_by=config.ADMIN_TELEGRAM_ID,
            keep_max=True,  # never lower a bigger active discount
        )
        current = await database.get_user_discount(telegram_id)
    except Exception as e:
        logger.exception("BROADCAST_GIFT_DISCOUNT_FAIL user=%s source=%s: %s", telegram_id, source, e)
        try:
            await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        except Exception:
            pass
        return

    kept = int((current or {}).get("discount_percent") or 0)
    if kept > percent:
        text = i18n_get_text(language, "main.discount_bigger_kept", percent=kept)
    else:
        text = i18n_get_text(language, "broadcast.gift_discount_applied", percent=percent, hours=hours)
    await _show_tariffs(callback, state, text)
    logger.info(
        "BROADCAST_GIFT_DISCOUNT user=%s source=%s pct=%s hours=%s kept=%s",
        telegram_id, source, percent, hours, kept,
    )


@broadcast_offers_router.callback_query(F.data.startswith("broadcast_gift_combo:"))
async def callback_broadcast_gift_combo(callback: CallbackQuery, state: FSMContext):
    """«🎁 Забрать подарок»: percent and hours come from the broadcast (wizard)."""
    await _answer(callback)
    percent, hours = 0, _GIFT_HOURS
    try:
        broadcast_id = int(callback.data.split(":")[1])
        discount = await database.get_broadcast_discount(broadcast_id) or {}
        percent = int(discount.get("discount_percent") or 0)
        hours = int(discount.get("discount_hours") or _GIFT_HOURS)
    except Exception as e:
        logger.warning("BROADCAST_GIFT_COMBO_LOOKUP_FAIL data=%s: %s", callback.data, e)
    if percent <= 0:
        # No discount behind the button: the tariff screen at the regular price.
        from app.handlers.common.screens import show_tariffs_main_screen
        await show_tariffs_main_screen(callback, state, force_new_message=True)
        return
    await _grant_gift_discount(callback, state, percent=percent, hours=hours, source="gift_combo")


@broadcast_offers_router.callback_query(F.data.startswith(("broadcast_gift_1m", "bcg1m:")))
async def callback_broadcast_gift_1m(callback: CallbackQuery, state: FSMContext):
    """«🎁 −30% на 1 месяц» (and the old bcg1m:* buttons of sent broadcasts)."""
    await _answer(callback)
    await _grant_period_discount(callback, state, offer=_GIFT1M, source="gift1m")


@broadcast_offers_router.callback_query(F.data.startswith(("broadcast_gift_3m", "bcg3m:")))
async def callback_broadcast_gift_3m(callback: CallbackQuery, state: FSMContext):
    """«🎁 Скидка 30% на 3 месяца» (and the old bcg3m:* buttons of sent broadcasts)."""
    await _answer(callback)
    await _grant_period_discount(callback, state, offer=_GIFT3M, source="gift3m")


@broadcast_offers_router.callback_query(F.data.startswith(("broadcast_gift_1y_40", "bcg1y40:")))
async def callback_broadcast_gift_1y_40(callback: CallbackQuery, state: FSMContext):
    """«🎁 1 год со скидкой 40%»: the 🏆 reveal, then the discount. The old
    bcg1y40:* buttons of sent broadcasts go straight to the discount."""
    await _answer(callback)
    if callback.data == "broadcast_gift_1y_40":
        chat_id = callback.message.chat.id if callback.message and callback.message.chat else callback.from_user.id
        try:
            reveal = await callback.bot.send_message(chat_id, _GIFT1Y40_REVEAL_EMOJI, parse_mode="HTML")
            await asyncio.sleep(_GIFT1Y40_REVEAL_PAUSE_SECONDS)
            await callback.bot.delete_message(chat_id, reveal.message_id)
        except Exception as e:
            logger.warning("BROADCAST_GIFT1Y40_REVEAL_FAIL user=%s: %s", callback.from_user.id, e)
    await _grant_period_discount(callback, state, offer=_GIFT1Y40, source="gift1y40")


@broadcast_offers_router.callback_query(F.data.startswith("broadcast_promo_traffic:"))
async def callback_broadcast_promo_traffic(callback: CallbackQuery):
    """User clicked 'Купить трафик промо' in broadcast — apply 1-day traffic discount."""
    await callback.answer()

    try:
        broadcast_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка", show_alert=True)
        return

    telegram_id = callback.from_user.id

    try:
        discount = await database.get_broadcast_discount(broadcast_id)
        discount_percent = discount.get("discount_percent", 0) if discount else 0

        if discount_percent > 0:
            # Apply 1-day traffic discount
            from datetime import timedelta
            expires_at = datetime.now(timezone.utc) + timedelta(days=1)
            await database.create_user_traffic_discount(
                telegram_id=telegram_id,
                discount_percent=discount_percent,
                expires_at=expires_at,
                created_by=config.ADMIN_TELEGRAM_ID,
            )

        # Build traffic packs message with discount applied
        language = await resolve_user_language(telegram_id)

        subscription = await database.get_subscription(telegram_id)
        if not subscription:
            await callback.message.answer(
                i18n_get_text(language, "traffic.no_subscription"),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text=i18n_get_text(language, "traffic.buy_subscription"),
                        callback_data="menu_buy_vpn",
                    )],
                ]),
                parse_mode="HTML",
            )
            return

        import math

        def _strikethrough(text: str) -> str:
            return "".join(ch + "\u0336" for ch in str(text))

        buttons = []
        for gb, pack in config.TRAFFIC_PACKS.items():
            base_price = pack["price"]
            if discount_percent > 0:
                final_price = math.ceil(base_price * (1 - discount_percent / 100))
                label = f"{gb} ГБ — {final_price} ₽  {_strikethrough(str(base_price))} ₽  (−{discount_percent}%)"
            else:
                label = f"{gb} ГБ — {base_price} ₽"
                if pack.get("discount"):
                    label += f"  {pack['discount']}"
            buttons.append([InlineKeyboardButton(
                text=label,
                callback_data=f"buy_traffic_pack:{gb}",
            )])

        buttons.append([InlineKeyboardButton(
            text="📦 Больше объёма →",
            callback_data=f"broadcast_promo_traffic_ext:{broadcast_id}",
        )])
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="traffic_info",
        )])

        text = i18n_get_text(language, "traffic.buy_title")
        if discount_percent > 0:
            text = f"🎁 Скидка {discount_percent}% на трафик применена! Действует 24 часа.\n\n" + text

        await callback.message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.exception(f"Error applying broadcast traffic promo discount: {e}")
        await callback.answer("Произошла ошибка, попробуйте позже", show_alert=True)


_GIFT_REVEAL_PERCENT_DEFAULT = 20  # fallback для рассылок без gift_reveal_percent в DB


_GIFT_REVEAL_HOURS = 48


_GIFT_REVEAL_EMOJI = '<tg-emoji emoji-id="5210956306952758910">👀</tg-emoji>'


_GIFT_REVEAL_PRESENT = '<tg-emoji emoji-id="5449800250032143374">🎁</tg-emoji>'


@broadcast_offers_router.callback_query(F.data.startswith("broadcast_gift_reveal:"))
async def callback_broadcast_gift_reveal(callback: CallbackQuery, state: FSMContext):
    """Кликнули «Посмотреть подарок» в рассылке — играем reveal-сценку
    и применяем скидку на 48ч, открываем экран тарифов.

    Процент скидки берётся из `broadcast_discounts.gift_reveal_percent`
    (админ выбрал в визарде: 20/25/30/35/40). Если по какой-то причине
    там пусто (старая рассылка до миграции 063, DB-ошибка) — fallback
    на legacy 20%, чтобы не оставлять юзера ни с чем.
    """
    await callback.answer()

    telegram_id = callback.from_user.id
    chat_id = callback.message.chat.id if callback.message else telegram_id

    # Определяем процент из БД. broadcast_id — второй элемент callback_data.
    percent = _GIFT_REVEAL_PERCENT_DEFAULT
    broadcast_id = None
    try:
        broadcast_id = int(callback.data.split(":", 1)[1])
        discount_row = await database.get_broadcast_discount(broadcast_id)
        gr_pct = (discount_row or {}).get("gift_reveal_percent")
        if gr_pct:
            percent = int(gr_pct)
            logger.info(
                "GIFT_REVEAL_CLICK broadcast_id=%s user=%s pct=%s (from DB)",
                broadcast_id, telegram_id, percent,
            )
        else:
            logger.warning(
                "GIFT_REVEAL_CLICK broadcast_id=%s user=%s pct=%s (FALLBACK — "
                "discount_row=%s, gift_reveal_percent=%s). Возможно: миграция 063 "
                "ещё не накатана / save упал при create /рассылка создана до фичи.",
                broadcast_id, telegram_id, percent,
                discount_row is not None, gr_pct,
            )
    except Exception as e:
        logger.warning(
            "GIFT_REVEAL_LOOKUP_FAIL broadcast_id=%s callback=%s err=%s — using default %s%%",
            broadcast_id, callback.data, e, _GIFT_REVEAL_PERCENT_DEFAULT,
        )

    try:
        # 1) эмодзи 👀 — интрига. Сохраняем message_id, чтобы удалить
        # его одновременно с появлением reveal-сообщения через 2 сек.
        eyes_msg = await callback.bot.send_message(
            chat_id,
            _GIFT_REVEAL_EMOJI,
            parse_mode="HTML",
        )

        # 2) держим паузу 2 секунды для эффекта
        await asyncio.sleep(2.0)

        # 3) удаляем «👀» (исчезает) и тут же шлём reveal — визуально
        # одно сменяется другим. Если delete упал (юзер сам удалил
        # сообщение или Telegram отказал) — это не критично, продолжаем.
        try:
            await callback.bot.delete_message(chat_id, eyes_msg.message_id)
        except Exception:
            pass

        # 4) reveal-сообщение с динамическим процентом
        await callback.bot.send_message(
            chat_id,
            f"<b>Для тебя подарок {percent}% скидка на любую подписку!</b> {_GIFT_REVEAL_PRESENT}",
            parse_mode="HTML",
        )

        # 5) применяем скидку %/48ч
        expires_at = datetime.now(timezone.utc) + timedelta(hours=_GIFT_REVEAL_HOURS)
        await database.create_user_discount(
            telegram_id=telegram_id,
            discount_percent=percent,
            expires_at=expires_at,
            created_by=config.ADMIN_TELEGRAM_ID,
            keep_max=True,  # never lower a bigger active discount
        )

        # 5) короткая пауза перед экраном тарифов — отделить визуально
        await asyncio.sleep(0.03)

        # 6) показываем экран выбора тарифов — get_user_discount внутри
        # автоматически подставит -20% на basic / plus / combo_basic /
        # combo_plus. Маркер `from_broadcast=True` нужен, чтобы кнопка
        # «Назад» с экрана выбора периода возвращала на этот же экран
        # выбора тарифов (а не на «Управление подпиской», куда
        # `menu_buy_vpn` уводит юзеров с активной подпиской).
        await state.update_data(from_broadcast=True)
        from app.handlers.common.screens import show_tariffs_main_screen
        await show_tariffs_main_screen(callback, state)

    except Exception as e:
        logger.exception(f"Error in broadcast_gift_reveal: {e}")
        await callback.answer("Произошла ошибка, попробуйте позже", show_alert=True)


@broadcast_offers_router.callback_query(F.data == "broadcast_back_to_tariffs")
async def callback_broadcast_back_to_tariffs(callback: CallbackQuery, state: FSMContext):
    """«Назад» с экрана выбора периода → обратно на экран выбора тарифа.

    Используется только в broadcast-flow (gift_reveal и подобных), где
    юзер ходит между «выбрать тариф → посмотреть период → назад». В
    обычном flow «Назад» по-прежнему ведёт на menu_buy_vpn («Управление
    подпиской»), это поведение не меняется.

    Маркер `from_broadcast=True` НЕ снимаем — юзер ещё внутри flow и
    может зайти в другой тариф. Снимется естественно при выходе из
    state (main menu, cabinet и т.п.).
    """
    try:
        await callback.answer()
    except Exception:
        pass
    from app.handlers.common.screens import show_tariffs_main_screen
    await show_tariffs_main_screen(callback, state)


@broadcast_offers_router.callback_query(F.data.startswith("broadcast_promo_traffic_ext:"))
async def callback_broadcast_promo_traffic_ext(callback: CallbackQuery):
    """Расширенные паки трафика (300+ ГБ) со скидкой из broadcast.

    Юзер нажимает «📦 Больше объёма →» на экране промо-трафика — попадает
    сюда. Скидка уже применена при первом клике (broadcast_promo_traffic),
    здесь только рендерим экран с extended-паками.
    """
    await callback.answer()

    try:
        broadcast_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка", show_alert=True)
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        discount = await database.get_broadcast_discount(broadcast_id)
        discount_percent = discount.get("discount_percent", 0) if discount else 0

        import math

        def _strikethrough(text: str) -> str:
            return "".join(ch + "̶" for ch in str(text))

        buttons = []
        for gb, pack in config.TRAFFIC_PACKS_EXTENDED.items():
            base_price = pack["price"]
            if discount_percent > 0:
                final_price = math.ceil(base_price * (1 - discount_percent / 100))
                label = f"{gb} ГБ — {final_price} ₽  {_strikethrough(str(base_price))} ₽  (−{discount_percent}%)"
            else:
                label = f"{gb} ГБ — {base_price} ₽"
                if pack.get("discount"):
                    label += f"  {pack['discount']}"
            buttons.append([InlineKeyboardButton(
                text=label,
                callback_data=f"buy_traffic_pack:{gb}",
            )])

        buttons.append([InlineKeyboardButton(
            text="← Основные паки",
            callback_data=f"broadcast_promo_traffic:{broadcast_id}",
        )])

        text = "📦 <b>Большие паки трафика</b>\n\nЧем больше пак — тем дешевле каждый гигабайт."
        if discount_percent > 0:
            text = f"🎁 Скидка {discount_percent}% активна 24 часа.\n\n" + text

        await callback.message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.exception(f"Error rendering extended traffic packs in broadcast: {e}")
        await callback.answer("Произошла ошибка, попробуйте позже", show_alert=True)
