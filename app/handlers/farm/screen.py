"""Экран фермы: один текст и одна клавиатура по текущему состоянию грядок.

ЧТО ЗДЕСЬ
    Единственная функция _render_farm. Её зовут из каждого обработчика после
    успешного действия — она же синхронизирует статусы грядок (растёт →
    созрело → погибло), рисует баннер шторма и собирает кнопки.

ПОЧЕМУ ОТДЕЛЬНО
    Отрисовка — самая часто правимая часть фермы (кнопка, порядок, формат
    суммы) и самая длинная. Пока она лежала посреди обработчиков, правка
    подписи на кнопке требовала пролистывания посадки, оплаты и штормов.

ЗДЕСЬ ЖЕ ЗАКРЫВАЮТСЯ «ЧАСИКИ»
    Telegram учитывает только ПЕРВЫЙ ответ на callback_query. Раньше
    обработчики отвечали пустым callback.answer() в самом начале — и все
    содержательные алерты («Урожай собран! +400 ₽») не показывались вообще.
    Теперь успешный путь отвечает отсюда, в самом конце, а путь с отказом —
    своим алертом. Ответ ровно один. Не добавляйте callback.answer() в
    начало обработчиков.

ЧТО ЛЕГКО СЛОМАТЬ
    Видимость грядки определяется условием plot_id < plot_count, и оно
    продублировано дважды — в тексте и в клавиатуре. Разъедутся — человек
    увидит грядку без кнопок или кнопку без грядки.
"""
from datetime import datetime, timezone

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.utils import safe_edit_text

# Общие с игровым меню элементы импортируются из game.py: справочник
# растений, цены грядки и плёнки. Держать их там правильно — меню игр
# живёт в game.py и ссылается на те же значения.
from app.handlers.game import (
    FARM_MAX_PLOTS,
    FARM_PLOT_PRICE_KOPECKS,
    PLANT_TYPES,
    farm_half_reward_kopecks,
    format_kopecks_rub,
    storm_shield_price_kopecks,
)
from app.handlers.farm.mechanics import _get_imminent_storm, _plant_name


async def _render_farm(callback, pool, farm_plots=None, plot_count=None, balance=None):
    """Render farm screen with current state"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    if farm_plots is None:
        farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    
    now = datetime.now(timezone.utc)
    
    # Sync statuses
    changed = False
    for plot in farm_plots:
        if plot["status"] == "growing" and plot.get("ready_at"):
            ready_at = datetime.fromisoformat(plot["ready_at"])
            if now >= ready_at:
                plot["status"] = "ready"
                changed = True
        if plot["status"] == "ready" and plot.get("dead_at"):
            dead_at = datetime.fromisoformat(plot["dead_at"])
            if now >= dead_at:
                plot["status"] = "dead"
                changed = True
    if changed:
        await database.save_farm_plots(telegram_id, farm_plots)
    
    # Imminent storm banner (only during the 24h announcement window)
    storm = await _get_imminent_storm()
    storm_active = storm is not None
    if storm_active:
        scheduled_at = storm["scheduled_at"]
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
        eta = scheduled_at - now
        eta_h = max(0, int(eta.total_seconds() // 3600))
        storm_banner = i18n_get_text(language, "farm.storm_banner", hours=eta_h)
    else:
        storm_banner = None

    # Build text (plot 0 always visible; plots 1-8 only if purchased, i.e. plot_id < plot_count)
    lines = [i18n_get_text(language, "farm.title") + "\n"]
    if storm_banner:
        lines.append(storm_banner)
    for plot in farm_plots:
        if plot["plot_id"] >= plot_count:
            continue
        i = plot["plot_id"]
        status = plot["status"]
        pt = plot.get("plant_type")
        plant = PLANT_TYPES.get(pt, {}) if pt else {}
        
        name = _plant_name(language, pt)

        if status == "empty":
            lines.append(i18n_get_text(language, "farm.plot_empty", num=i + 1))
        elif status == "growing":
            ready_at = datetime.fromisoformat(plot["ready_at"])
            remaining = ready_at - now
            days = remaining.days
            hours = remaining.seconds // 3600
            # Значок щита клеим к названию: отдельного плейсхолдера в ключе
            # нет, а вводить его — значит трогать перевод во всех 7 языках.
            shield_mark = " 🛡" if plot.get("storm_shielded") else ""
            lines.append(i18n_get_text(
                language, "farm.plot_growing",
                num=i + 1, name=f"{name}{shield_mark}", days=days, hours=hours,
            ))
        elif status == "ready":
            lines.append(i18n_get_text(
                language, "farm.plot_ready",
                num=i + 1, emoji=plant.get("emoji", "🌿"), name=name,
            ))
        elif status == "dead":
            lines.append(i18n_get_text(language, "farm.plot_dead", num=i + 1, name=name))

    lines.append(i18n_get_text(language, "farm.balance", balance=balance / 100))
    text = "\n".join(lines)
    
    # Build keyboard (same visibility: plot_id < plot_count)
    buttons = []
    for plot in farm_plots:
        if plot["plot_id"] >= plot_count:
            continue
        i = plot["plot_id"]
        status = plot["status"]
        pt = plot.get("plant_type")
        plant = PLANT_TYPES.get(pt, {}) if pt else {}
        
        if status == "empty":
            if storm_active:
                buttons.append([InlineKeyboardButton(
                    text=i18n_get_text(language, "farm.button_plant_storm_blocked", num=i + 1),
                    callback_data="farm_noop"
                )])
            else:
                buttons.append([InlineKeyboardButton(
                    text=i18n_get_text(language, "farm.button_plant", num=i + 1),
                    callback_data=f"farm_choose_{i}"
                )])
        elif status == "growing":
            # Storm controls — only during the 24h announcement window, only if not already shielded.
            # Planting is disabled during a storm (see callback_farm_choose_plant), so every
            # growing plot at this point was planted BEFORE the storm — no replant exploit possible.
            if storm_active and not plot.get("storm_shielded"):
                shield_cost_kopecks = storm_shield_price_kopecks(int(plant.get("reward", 0)))
                shield_cost_rub = shield_cost_kopecks // 100
                # Витрина раннего сбора обязана называть ТУ ЖЕ сумму, которую
                # потом начислит storm.py. Раньше здесь делили на 200 (сразу в
                # рубли, с отбрасыванием копеек), а начисляли reward // 2 в
                # копейках: дуб 5300 → на кнопке «+26 ₽», на баланс 26,50 ₽.
                # Считать половину можно только через farm_half_reward_kopecks,
                # показывать — только через format_kopecks_rub.
                half_reward_shown = format_kopecks_rub(
                    farm_half_reward_kopecks(int(plant.get("reward", 0)))
                )
                buttons.append([InlineKeyboardButton(
                    text=i18n_get_text(
                        language, "farm.button_shield", num=i + 1, price=shield_cost_rub,
                    ),
                    callback_data=f"farm_shield:{i}"
                )])
                buttons.append([InlineKeyboardButton(
                    text=i18n_get_text(
                        language, "farm.button_early", num=i + 1, reward=half_reward_shown,
                    ),
                    callback_data=f"farm_early:{i}"
                )])

            # Water button
            row = []
            water_used = plot.get("water_used_at")
            can_water = not water_used or (now - datetime.fromisoformat(water_used)).total_seconds() >= 86400
            fert_used = plot.get("fertilizer_used_at")
            can_fert = not fert_used or (now - datetime.fromisoformat(fert_used)).total_seconds() >= 86400

            if can_water:
                row.append(InlineKeyboardButton(
                    text=i18n_get_text(language, "farm.button_water", num=i + 1),
                    callback_data=f"farm_water_{i}",
                ))
            if can_fert:
                row.append(InlineKeyboardButton(
                    text=i18n_get_text(language, "farm.button_fertilize", num=i + 1),
                    callback_data=f"farm_fert_{i}",
                ))
            if row:
                buttons.append(row)
            # Always show dig button for growing plots
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "farm.button_dig", num=i + 1),
                callback_data=f"farm_dig_{i}"
            )])
        elif status == "ready":
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(
                    language, "farm.button_harvest",
                    emoji=plant.get("emoji", ""), num=i + 1,
                    reward=plant.get("reward", 0) // 100,
                ),
                callback_data=f"farm_harvest_{i}"
            )])
        elif status == "dead":
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "farm.button_remove", num=i + 1),
                callback_data=f"farm_remove_{i}"
            )])
    
    # Buy plot button
    if plot_count < FARM_MAX_PLOTS:
        price = FARM_PLOT_PRICE_KOPECKS
        price_rub = price // 100
        remaining = FARM_MAX_PLOTS - plot_count
        if balance >= price:
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(
                    language, "farm.button_buy_plot_slots",
                    price=price_rub, slots=remaining,
                ),
                callback_data="farm_buy_plot"
            )])
        else:
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(
                    language, "farm.button_buy_plot_slots_disabled",
                    price=price_rub, slots=remaining,
                ),
                callback_data="farm_noop"
            )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "farm.button_guide"),
        url="https://telegra.ph/Instrukciya-Ferma-02-20"
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "farm.back_to_games"),
        callback_data="games_menu",
    )])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    try:
        await safe_edit_text(callback.message,text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise

    # Закрываем «часики» на кнопке здесь, в конце успешного пути.
    #
    # Раньше обработчики отвечали пустым callback.answer() в самом начале —
    # «чтобы кнопка не крутилась». Telegram учитывает только ПЕРВЫЙ ответ на
    # callback_query, поэтому все последующие содержательные алерты («Урожай
    # собран! +400 ₽», «Вы уже удобряли сегодня») не показывались вообще.
    # Человек нажимал и не понимал, случилось что-нибудь или нет.
    #
    # Теперь ранних ответов нет: успешный путь отвечает отсюда, а путь с
    # отказом — своим алертом. В обоих случаях ответ ровно один.
    try:
        await callback.answer()
    except Exception:
        # Callback мог устареть (прошло больше таймаута Telegram) — экран
        # уже перерисован, и это не повод считать действие неудачным.
        pass
