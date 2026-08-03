"""Мини-игра «Ферма»: посадка, уход, сбор урожая и штормы.

КАК УСТРОЕНА ИГРА
    У пользователя есть грядки. На грядку сажают растение, поливают и
    удобряют (это ускоряет созревание), а созревшее собирают — награда
    зачисляется на баланс в рублях.

ПОЧЕМУ ЗДЕСЬ СТОЛЬКО АТОМАРНОСТИ
    Награда — реальные деньги на балансе, поэтому любое действие, которое
    начисляет или списывает, обязано выполняться одной транзакцией под
    advisory-локом. Раньше сбор урожая читал состояние, начислял награду и
    только потом сбрасывал грядку тремя отдельными запросами — двойной клик
    начислял награду несколько раз. Теперь этим занимается
    database.harvest_plot_atomic, и обходить её нельзя.

ШТОРМ
    Периодическое событие, уничтожающее незащищённые растения. Действует
    одинаково на всех, независимо от того, был ли игрок в боте: раньше
    офлайновые получали автосбор половины награды, из-за чего заходить в
    бота во время шторма было невыгодно. Спастись можно щитом или ранним
    сбором — оба действия требуют присутствия.

ЯЗЫК ИНТЕРФЕЙСА
    Здесь не должно остаться ни одной русской строки в коде. Раньше вся
    ферма была собрана из литералов, и пользователь с любым из шести
    остальных языков получал русский экран целиком, а следом — русские пуши
    про шторм. Тексты живут в app/i18n под ключами farm.*, названия культур —
    farm.plant_<ключ>. PLANT_TYPES остаётся справочником механики: имена в
    нём русские и на экран напрямую не попадают, для этого есть _plant_name.

ЧТО ЛЕГКО СЛОМАТЬ
    Награды растений и цена щита заданы в копейках. Любая новая механика,
    выдающая ценность, обязана проходить через месячный потолок
    config.GAME_MONTHLY_DAYS_CAP, если выдаёт дни подписки.
"""
import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Set

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter
from aiogram.exceptions import TelegramBadRequest

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.keyboards import get_back_keyboard
from app.handlers.common.states import BomberState
from app.handlers.common.utils import safe_edit_text

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query.middleware()
async def require_active_subscription(handler, event: CallbackQuery, data: dict):
    """Пускать на ферму только действующих подписчиков.

    Зачем middleware, а не проверка в каждом обработчике: ферма начисляет
    реальный баланс (см. шапку модуля), и хендлеров у неё полтора десятка —
    посадка, полив, удобрение, сбор, ранний сбор, покупка грядки, щит.
    Забыть проверку в одном из них = неплательщик продолжает майнить деньги.
    Один вход в роутер закрывает все callback'и разом, включая те, что
    добавят позже.

    Почему это вообще возможно без проверки: старое сообщение с инлайн-
    клавиатурой остаётся в чате навсегда. Подписка истекла, была отменена
    или возвращена — а кнопки «Собрать» в старом экране по-прежнему живые.

    Меню игр и остальные игры проверяют подписку сами (app/handlers/game.py),
    здесь повторяем ту же семантику: get_subscription возвращает строку
    только при status='active' и expires_at в будущем.
    """
    telegram_id = event.from_user.id if event.from_user else 0
    try:
        subscription = await database.get_subscription(telegram_id)
    except Exception as e:
        # База недоступна — не наказываем плательщика: пропускаем дальше,
        # обработчик сам упрётся в ensure_db_ready_callback и покажет ошибку.
        logger.warning("FARM_GUARD_DB_ERROR user=%s: %s", telegram_id, e)
        return await handler(event, data)

    if subscription:
        return await handler(event, data)

    logger.info("FARM_GUARD_BLOCKED user=%s no_active_subscription", telegram_id)
    language = await resolve_user_language(telegram_id)
    await event.answer(
        i18n_get_text(language, "farm.paywall"),
        show_alert=True,
    )
    return None

# Общие с игровым меню элементы импортируются из game.py: клавиатура возврата,
# справочник растений, цены грядки и щита. Держать их там правильно — меню игр
# живёт в game.py и ссылается на те же значения.
from app.handlers.game import (  # noqa: E402
    FARM_MAX_PLOTS,
    FARM_PLOT_PRICE_KOPECKS,
    PLANT_TYPES,
    get_games_back_keyboard,
    storm_shield_price_kopecks,
)


# Сколько шторм может опаздывать, прежде чем считать его зависшим.
#
# Пока шторм «объявлен и не исполнен», посадка выключена у всех. Если воркер
# не отработал — упал, был остановлен на деплое, база была недоступна — шторм
# остаётся в этом состоянии навсегда, и ферма мертва для всех пользователей
# без единой ошибки в интерфейсе. Через это окно перестаём считать его
# действующим: лучше пропустить одно событие, чем заблокировать игру.
STORM_STALE_AFTER_HOURS = 6


# Сколько времени должно оставаться до шторма, чтобы имело смысл выставлять
# счёт на плёнку.
#
# Оплата картой или через СБП — это уход на страницу платёжки, ввод реквизитов
# и ожидание вебхука; на практике от нажатия «Картой» до применения щита
# проходят единицы минут, а при задержке провайдера — десятки. Если до удара
# осталась минута, деньги уйдут, а плёнка гарантированно опоздает: шторм
# отработает раньше вебхука и грядка погибнет. Оплату с баланса это не
# касается — она мгновенная и остаётся доступной до самого удара.
SHIELD_INVOICE_MIN_LEAD_MINUTES = 30


# Максимальная доля времени роста, которую можно снять поливом и удобрением.
#
# Без потолка уход снимал 8 часов за каждые 24 часа реального времени, то есть
# треть срока: дуб созревал за 24 дня вместо 32, а пассивный доход фермы рос в
# полтора раза мимо всех расчётов баланса.
FARM_BOOST_MAX_FRACTION = 0.20


def _plant_name(language: str, plant_key) -> str:
    """Название культуры на языке пользователя.

    В PLANT_TYPES имена лежат по-русски — это справочник механики, а не
    словарь интерфейса. Ключ собирается конкатенацией (а не f-строкой),
    чтобы проверка ключей по исходникам видела префикс farm.plant_.
    """
    fallback = (PLANT_TYPES.get(plant_key) or {}).get("name", "")
    if not plant_key:
        return fallback
    key = "farm.plant_" + str(plant_key)
    text = i18n_get_text(language, key, fallback)
    # Пустой запасной текст get_text не считает запасным и возвращает сам
    # ключ. Для культуры, которой нет в справочнике (битые данные, старый
    # сорт), человек увидел бы на грядке строку «farm.plant_xxx».
    return "" if text == key else text


def _storm_seconds_left(storm) -> float:
    """Сколько секунд осталось до удара шторма (может быть отрицательным)."""
    scheduled_at = storm.get("scheduled_at")
    if scheduled_at is None:
        return 0.0
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    return (scheduled_at - datetime.now(timezone.utc)).total_seconds()


def _invoice_can_arrive_in_time(storm) -> bool:
    """Успеет ли внешний платёж за плёнку дойти до удара шторма.

    См. SHIELD_INVOICE_MIN_LEAD_MINUTES: продавать плёнку, которая заведомо
    не применится, нельзя — это спор о деньгах, а не игровая механика.
    """
    return _storm_seconds_left(storm) >= SHIELD_INVOICE_MIN_LEAD_MINUTES * 60


def _apply_growth_boost(plot: dict, hours: int) -> bool:
    """Ускорить созревание на hours часов, но не больше общего потолка.

    Что чинится. Полив (−6 ч) и удобрение (−2 ч) просто вычитали время из
    ready_at без нижней границы, а dead_at оставался на месте. Отсюда две
    беды: срок роста сжимался на треть (мимо расчёта экономики фермы) и окно
    сбора растягивалось с положенных 24 часов на всё накопленное ускорение.

    Теперь суммарное ускорение ограничено долей FARM_BOOST_MAX_FRACTION от
    исходного времени роста, а dead_at едет ровно на столько же, на сколько
    ready_at, — окно сбора всегда остаётся 24-часовым.

    Возвращает False, если лимит ускорения уже выбран (менять нечего).
    """
    ready_at = datetime.fromisoformat(plot["ready_at"])
    plant = PLANT_TYPES.get(plot.get("plant_type")) or {}
    grow_seconds = int(plant.get("days", 0)) * 86400
    planted_at_raw = plot.get("planted_at")

    if grow_seconds <= 0 or not planted_at_raw:
        # Культуры нет в справочнике или грядка старого формата без
        # planted_at: посчитать потолок не от чего. Награды у такой грядки
        # тоже нет (reward берётся из того же справочника), поэтому просто
        # применяем ускорение как раньше — но dead_at двигаем.
        speedup = timedelta(hours=hours)
    else:
        base_ready = datetime.fromisoformat(planted_at_raw) + timedelta(seconds=grow_seconds)
        used_seconds = (base_ready - ready_at).total_seconds()
        allowance = grow_seconds * FARM_BOOST_MAX_FRACTION - used_seconds
        if allowance <= 0:
            return False
        speedup = timedelta(seconds=min(hours * 3600, allowance))

    plot["ready_at"] = (ready_at - speedup).isoformat()
    dead_at_raw = plot.get("dead_at")
    if dead_at_raw:
        plot["dead_at"] = (datetime.fromisoformat(dead_at_raw) - speedup).isoformat()
    return True


async def _get_imminent_storm():
    """Действующий шторм: объявлен, не исполнен и не просрочен.

    Просроченный шторм игнорируется — см. STORM_STALE_AFTER_HOURS.
    """
    storm = await database.get_pending_storm()
    if not storm:
        return None
    if not storm.get("announced_at") or storm.get("executed_at"):
        return None

    scheduled_at = storm.get("scheduled_at")
    if scheduled_at is not None:
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
        overdue = datetime.now(timezone.utc) - scheduled_at
        if overdue > timedelta(hours=STORM_STALE_AFTER_HOURS):
            logger.warning(
                "STORM_STALE storm_id=%s scheduled_at=%s overdue_hours=%.1f — "
                "считаем шторм зависшим, посадка разблокирована",
                storm.get("id"), scheduled_at, overdue.total_seconds() / 3600,
            )
            return None
    return storm


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
                half_reward_rub = int(plant.get("reward", 0)) // 200  # half of reward, in RUB
                buttons.append([InlineKeyboardButton(
                    text=i18n_get_text(
                        language, "farm.button_shield", num=i + 1, price=shield_cost_rub,
                    ),
                    callback_data=f"farm_shield:{i}"
                )])
                buttons.append([InlineKeyboardButton(
                    text=i18n_get_text(
                        language, "farm.button_early", num=i + 1, reward=half_reward_rub,
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


@router.callback_query(F.data == "game_farm")
async def callback_game_farm(callback: CallbackQuery):
    """Farm game main screen"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    await callback.answer()
    
    pool = await database.get_pool()
    if not pool:
        telegram_id = callback.from_user.id
        language = await resolve_user_language(telegram_id)
        await safe_edit_text(callback.message,
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
            parse_mode="HTML",
        )
        return
    
    await _render_farm(callback, pool)


@router.callback_query(F.data.startswith("farm_choose_"))
async def callback_farm_choose_plant(callback: CallbackQuery, state: FSMContext):
    """Show plant selection screen"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # During an announced storm planting is disabled to prevent the
    # replant + early-harvest loop and to keep the rule simple for players.
    if await _get_imminent_storm() is not None:
        await callback.answer(
            i18n_get_text(language, "farm.storm_planting_blocked"),
            show_alert=True,
        )
        return

    plot_id = int(callback.data.split("_")[-1])

    buttons = []
    for key, plant in PLANT_TYPES.items():
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(
                language, "farm.plant_info",
                emoji=plant["emoji"], name=_plant_name(language, key),
                days=plant["days"], reward=plant["reward"] // 100,
            ),
            callback_data=f"farm_plant_{plot_id}_{key}"
        )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "farm.back"), callback_data="game_farm",
    )])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await safe_edit_text(callback.message,
        i18n_get_text(language, "farm.choose_plant_title", num=plot_id + 1),
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("farm_plant_"))
async def callback_farm_plant(callback: CallbackQuery, state: FSMContext):
    """Plant a seed"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # Server-side gate — must match the farm_choose_ guard.  A user could
    # otherwise hand-craft farm_plant_<plot>_<type> to bypass the menu hide.
    if await _get_imminent_storm() is not None:
        await callback.answer(
            i18n_get_text(language, "farm.storm_planting_blocked"),
            show_alert=True,
        )
        return

    parts = callback.data.split("_")
    plot_id = int(parts[2])
    plant_type = parts[3]

    if plant_type not in PLANT_TYPES:
        await callback.answer(
            i18n_get_text(language, "farm.error_unknown_plant"), show_alert=True,
        )
        return
    
    pool = await database.get_pool()
    if not pool:
        await safe_edit_text(callback.message,
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
            parse_mode="HTML",
        )
        return
    
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    
    # Find plot
    plot = None
    for p in farm_plots:
        if p["plot_id"] == plot_id:
            plot = p
            break
    
    if not plot or plot["status"] != "empty":
        await callback.answer(
            i18n_get_text(language, "farm.error_plot_unavailable"), show_alert=True,
        )
        return

    now = datetime.now(timezone.utc)
    grow_seconds = PLANT_TYPES[plant_type]["days"] * 86400
    ready_at = now + timedelta(seconds=grow_seconds)
    dead_at = ready_at + timedelta(hours=24)
    
    plot["status"] = "growing"
    plot["plant_type"] = plant_type
    plot["planted_at"] = now.isoformat()
    plot["ready_at"] = ready_at.isoformat()
    plot["dead_at"] = dead_at.isoformat()
    plot["notified_ready"] = False
    plot["notified_12h"] = False
    plot["notified_dead"] = False
    plot["water_used_at"] = None
    plot["fertilizer_used_at"] = None
    
    await database.save_farm_plots(telegram_id, farm_plots)
    await _render_farm(callback, pool, farm_plots, plot_count, balance)


@router.callback_query(F.data.startswith("farm_water_"))
async def callback_farm_water(callback: CallbackQuery, state: FSMContext):
    """Water a plant"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    plot_id = int(callback.data.split("_")[-1])
    
    pool = await database.get_pool()
    if not pool:
        await safe_edit_text(callback.message,
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
            parse_mode="HTML",
        )
        return
    
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    
    plot = None
    for p in farm_plots:
        if p["plot_id"] == plot_id:
            plot = p
            break
    
    if not plot or plot["status"] != "growing":
        await callback.answer(
            i18n_get_text(language, "farm.error_plot_unavailable"), show_alert=True,
        )
        return

    now = datetime.now(timezone.utc)
    water_used = plot.get("water_used_at")
    if water_used:
        water_time = datetime.fromisoformat(water_used)
        if (now - water_time).total_seconds() < 86400:
            await callback.answer(
                i18n_get_text(language, "farm.error_already_watered"), show_alert=True,
            )
            return

    # Полив снимает 6 часов, но общий потолок ускорения считает _apply_growth_boost.
    # Если лимит выбран — суточный кулдаун не сжигаем: человек не виноват, что
    # растение уже на пределе, пусть попробует на другой грядке.
    if not _apply_growth_boost(plot, hours=6):
        await callback.answer(
            i18n_get_text(language, "farm.error_boost_limit"), show_alert=True,
        )
        return
    plot["water_used_at"] = now.isoformat()

    await database.save_farm_plots(telegram_id, farm_plots)
    await _render_farm(callback, pool, farm_plots, plot_count, balance)


@router.callback_query(F.data.startswith("farm_fert_"))
async def callback_farm_fert(callback: CallbackQuery, state: FSMContext):
    """Fertilize a plant"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    plot_id = int(callback.data.split("_")[-1])
    
    pool = await database.get_pool()
    if not pool:
        await safe_edit_text(callback.message,
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
            parse_mode="HTML",
        )
        return
    
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    
    plot = None
    for p in farm_plots:
        if p["plot_id"] == plot_id:
            plot = p
            break
    
    if not plot or plot["status"] != "growing":
        await callback.answer(
            i18n_get_text(language, "farm.error_plot_unavailable"), show_alert=True,
        )
        return

    now = datetime.now(timezone.utc)
    fert_used = plot.get("fertilizer_used_at")
    if fert_used:
        fert_time = datetime.fromisoformat(fert_used)
        if (now - fert_time).total_seconds() < 86400:
            await callback.answer(
                i18n_get_text(language, "farm.error_already_fertilized"), show_alert=True,
            )
            return

    # Удобрение снимает 2 часа — тот же общий потолок, что и у полива.
    if not _apply_growth_boost(plot, hours=2):
        await callback.answer(
            i18n_get_text(language, "farm.error_boost_limit"), show_alert=True,
        )
        return
    plot["fertilizer_used_at"] = now.isoformat()

    await database.save_farm_plots(telegram_id, farm_plots)
    await _render_farm(callback, pool, farm_plots, plot_count, balance)


@router.callback_query(F.data.startswith("farm_harvest_"))
async def callback_farm_harvest(callback: CallbackQuery, state: FSMContext):
    """Harvest a ready plant"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    plot_id = int(callback.data.split("_")[-1])
    
    pool = await database.get_pool()
    if not pool:
        await safe_edit_text(callback.message,
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
            parse_mode="HTML",
        )
        return
    
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    
    plot = None
    for p in farm_plots:
        if p["plot_id"] == plot_id:
            plot = p
            break
    
    if not plot or plot["status"] != "ready":
        await callback.answer(
            i18n_get_text(language, "farm.error_not_ready"), show_alert=True,
        )
        return

    plant_type = plot.get("plant_type")
    plant = PLANT_TYPES.get(plant_type, {})
    reward_kopecks = plant.get("reward", 0)
    reward_rubles = reward_kopecks / 100.0

    # Сброс грядки и начисление — одной транзакцией под advisory-локом.
    # Раньше это были три отдельные операции, и двойной клик начислял награду
    # несколько раз: обе копии обработчика успевали пройти проверку статуса.
    success, reason = await database.harvest_plot_atomic(
        telegram_id=telegram_id,
        plot_id=plot_id,
        reward_kopecks=int(reward_kopecks),
        source="farm_harvest",
        description=f"Farm harvest: {plant.get('name', 'unknown')}",
    )

    if not success:
        if reason == "plot_wrong_status":
            # Сюда попадает второй клик по той же грядке — это не ошибка.
            await callback.answer(
                i18n_get_text(language, "farm.error_already_harvested"), show_alert=True,
            )
        else:
            logger.warning(
                "FARM_HARVEST_FAILED user=%s plot=%s reason=%s",
                telegram_id, plot_id, reason,
            )
            await callback.answer(
                i18n_get_text(language, "farm.error_harvest_failed"), show_alert=True,
            )
        return

    # Refresh balance
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    await _render_farm(callback, pool, farm_plots, plot_count, balance)

    await callback.answer(
        i18n_get_text(language, "farm.harvest_success", reward=f"{reward_rubles:.0f}"),
        show_alert=True,
    )


@router.callback_query(F.data.startswith("farm_remove_"))
async def callback_farm_remove(callback: CallbackQuery, state: FSMContext):
    """Remove dead plant - show confirmation"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    plot_id = int(callback.data.split("_")[-1])
    
    # Check if this is a confirmation
    if callback.data.startswith("farm_remove_confirm_"):
        plot_id = int(callback.data.split("_")[-1])
        
        pool = await database.get_pool()
        if not pool:
            await safe_edit_text(callback.message,
                i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
                reply_markup=get_games_back_keyboard(language),
                parse_mode="HTML",
            )
            return
        
        farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
        
        plot = None
        for p in farm_plots:
            if p["plot_id"] == plot_id:
                plot = p
                break
        
        if plot and plot["status"] == "dead":
            # Reset plot
            plot["status"] = "empty"
            plot["plant_type"] = None
            plot["planted_at"] = None
            plot["ready_at"] = None
            plot["dead_at"] = None
            plot["notified_ready"] = False
            plot["notified_12h"] = False
            plot["notified_dead"] = False
            plot["water_used_at"] = None
            plot["fertilizer_used_at"] = None
            
            await database.save_farm_plots(telegram_id, farm_plots)
            await _render_farm(callback, pool, farm_plots, plot_count, balance)
        return
    
    # Show confirmation dialog
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "farm.remove_yes"),
            callback_data=f"farm_remove_confirm_{plot_id}"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "farm.remove_no"),
            callback_data="farm_noop"
        )]
    ])

    await safe_edit_text(callback.message,
        i18n_get_text(language, "farm.remove_confirm"),
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "farm_buy_plot")
async def callback_farm_buy_plot(callback: CallbackQuery, state: FSMContext):
    """Buy a new plot"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    pool = await database.get_pool()
    if not pool:
        await safe_edit_text(callback.message,
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
            parse_mode="HTML",
        )
        return
    
    # Покупка грядки одной транзакцией: списание, грядка и счётчик вместе.
    # Раньше это были три отдельных запроса — сбой между ними снимал деньги
    # без грядки, а параллельные клики создавали две грядки с одним номером.
    ok, reason = await database.buy_farm_plot_atomic(
        telegram_id,
        price_kopecks=FARM_PLOT_PRICE_KOPECKS,
        max_plots=FARM_MAX_PLOTS,
        description=f"Покупка грядки за {FARM_PLOT_PRICE_KOPECKS // 100} ₽",
    )
    if not ok:
        # Отказ объясняем текстом, а не молчанием: «максимум грядок» и
        # «не хватает денег» — разные истории для человека.
        messages = {
            "max_plots_reached": "farm.max_plots_reached",
            "insufficient_balance": "farm.insufficient_funds",
        }
        await callback.answer(
            i18n_get_text(language, messages.get(reason, "farm.buy_plot_error")),
            show_alert=True,
        )
        if reason not in messages:
            logger.warning(
                "FARM_BUY_PLOT_FAILED user=%s reason=%s", telegram_id, reason
            )
        return

    # Refresh balance
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    await _render_farm(callback, pool, farm_plots, plot_count, balance)


@router.callback_query(F.data.startswith("farm_dig_") & ~F.data.startswith("farm_dig_confirm_"), StateFilter("*"))
async def callback_farm_dig(callback: CallbackQuery, state: FSMContext):
    """Show confirmation dialog for digging up a plant"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    plot_id = int(callback.data.split("_")[-1])
    
    pool = await database.get_pool()
    if not pool:
        await safe_edit_text(callback.message,
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
            parse_mode="HTML",
        )
        return
    
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    plot = next((p for p in farm_plots if p["plot_id"] == plot_id), None)
    
    if not plot or plot["status"] != "growing":
        await callback.answer(
            i18n_get_text(language, "farm.dig_unavailable"), show_alert=True,
        )
        return

    plant_name = _plant_name(language, plot.get("plant_type", ""))

    # Show confirmation with inline keyboard
    confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=i18n_get_text(language, "farm.dig_yes"),
                callback_data=f"farm_dig_confirm_{plot_id}"
            ),
            InlineKeyboardButton(
                text=i18n_get_text(language, "farm.remove_no"),
                callback_data="game_farm"
            )
        ]
    ])

    await safe_edit_text(callback.message,
        i18n_get_text(language, "farm.dig_confirm", name=plant_name, num=plot_id + 1),
        reply_markup=confirm_keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("farm_dig_confirm_"), StateFilter("*"))
async def callback_farm_dig_confirm(callback: CallbackQuery, state: FSMContext):
    """Confirm and execute digging up a plant"""
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    plot_id = int(callback.data.split("_")[-1])
    
    pool = await database.get_pool()
    if not pool:
        await safe_edit_text(callback.message,
            i18n_get_text(language, "errors.database_unavailable", "Database temporarily unavailable"),
            reply_markup=get_games_back_keyboard(language),
            parse_mode="HTML",
        )
        return
    
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    plot = next((p for p in farm_plots if p["plot_id"] == plot_id), None)
    
    if not plot:
        await callback.answer(
            i18n_get_text(language, "farm.error_plot_not_found"), show_alert=True,
        )
        return
    
    # Reset plot to empty
    plot["status"] = "empty"
    plot["plant_type"] = None
    plot["planted_at"] = None
    plot["ready_at"] = None
    plot["dead_at"] = None
    plot["notified_ready"] = False
    plot["notified_12h"] = False
    plot["notified_dead"] = False
    plot["water_used_at"] = None
    plot["fertilizer_used_at"] = None
    
    await database.save_farm_plots(telegram_id, farm_plots)
    await callback.answer(
        i18n_get_text(language, "farm.dig_success"), show_alert=True,
    )
    await _render_farm(callback, pool, farm_plots=farm_plots, 
                       plot_count=plot_count, balance=balance)


@router.callback_query(F.data == "farm_noop")
async def callback_farm_noop(callback: CallbackQuery):
    """No-op handler for disabled buttons"""
    await callback.answer()


# ════════════════════════════════════════════════════════════════════════
# FARM STORM — shield purchase + early harvest
# ════════════════════════════════════════════════════════════════════════

def _parse_plot_id(callback_data: str, prefix: str) -> int:
    """Extract integer plot_id from 'prefix:<n>' callback data; -1 on parse fail."""
    try:
        return int(callback_data.split(":", 1)[1])
    except (ValueError, IndexError):
        return -1


async def _find_growing_plot(telegram_id: int, plot_id: int):
    """Return (farm_plots, plot_count, balance, plot_dict) or (..., None) if
    the plot is missing / not growing.  Caller short-circuits."""
    farm_plots, plot_count, balance = await database.get_farm_data(telegram_id)
    target = None
    for p in farm_plots:
        if int(p.get("plot_id", -1)) == plot_id:
            target = p
            break
    if target is None or target.get("status") != "growing":
        return farm_plots, plot_count, balance, None
    return farm_plots, plot_count, balance, target


async def _shield_invoice_allowed(callback, language: str, telegram_id: int, plot_id: int) -> bool:
    """Можно ли сейчас выставлять счёт на плёнку. Отказ объясняет сам.

    Проверка повторяется перед каждым созданием счёта, а не только на экране
    выбора оплаты: сообщение с кнопками «Картой»/«СБП» остаётся в чате и через
    час, когда шторм уже на пороге или прошёл.
    """
    storm = await _get_imminent_storm()
    if storm is None:
        await callback.answer(
            i18n_get_text(language, "farm.shield_no_storm"), show_alert=True,
        )
        return False
    if not _invoice_can_arrive_in_time(storm):
        logger.info(
            "FARM_SHIELD_TOO_LATE user=%s plot=%s seconds_left=%.0f",
            telegram_id, plot_id, _storm_seconds_left(storm),
        )
        await callback.answer(
            i18n_get_text(
                language, "farm.shield_invoice_too_late",
                minutes=SHIELD_INVOICE_MIN_LEAD_MINUTES,
            ),
            show_alert=True,
        )
        return False
    return True


@router.callback_query(F.data.startswith("farm_shield:"))
async def callback_farm_shield(callback: CallbackQuery):
    """🛡 Накрыть — pay via balance if enough, else show Lava/SBP screen."""
    if not await ensure_db_ready_callback(callback):
        return
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    storm = await _get_imminent_storm()
    if storm is None:
        await callback.answer(
            i18n_get_text(language, "farm.shield_no_storm"), show_alert=True,
        )
        return

    plot_id = _parse_plot_id(callback.data, "farm_shield")
    if plot_id < 0:
        return
    farm_plots, plot_count, balance, plot = await _find_growing_plot(telegram_id, plot_id)
    if plot is None:
        await callback.answer(
            i18n_get_text(language, "farm.shield_plot_not_growing"), show_alert=True,
        )
        return
    if plot.get("storm_shielded"):
        await callback.answer(
            i18n_get_text(language, "farm.shield_already"), show_alert=True,
        )
        return

    plant = PLANT_TYPES.get(plot.get("plant_type"), {})
    shield_cost = storm_shield_price_kopecks(int(plant.get("reward", 0)))
    shield_cost_rub = shield_cost // 100

    if balance >= shield_cost:
        ok, reason = await database.apply_storm_shield_atomic(
            telegram_id, plot_id, shield_cost, deduct_balance=True,
        )
        if ok:
            await callback.answer(
                i18n_get_text(language, "farm.shield_applied", price=shield_cost_rub),
                show_alert=True,
            )
        else:
            # Внутреннюю причину отказа показывать человеку нечего — она
            # техническая ("plot_not_growing"), да ещё и по-английски.
            logger.info(
                "FARM_SHIELD_BALANCE_FAILED user=%s plot=%s reason=%s",
                telegram_id, plot_id, reason,
            )
            await callback.answer(
                i18n_get_text(language, "farm.shield_failed"), show_alert=True,
            )
        pool = await database.get_pool()
        await _render_farm(callback, pool)
        return

    # Денег на балансе не хватает — остаётся внешняя оплата. Но если до удара
    # меньше SHIELD_INVOICE_MIN_LEAD_MINUTES, счёт выставлять нельзя: платёж
    # не успеет дойти, грядка погибнет, а деньги уже уйдут — и дальше это
    # разбирает поддержка вручную.
    if not _invoice_can_arrive_in_time(storm):
        logger.info(
            "FARM_SHIELD_TOO_LATE user=%s plot=%s seconds_left=%.0f",
            telegram_id, plot_id, _storm_seconds_left(storm),
        )
        await callback.answer(
            i18n_get_text(
                language, "farm.shield_invoice_too_late",
                minutes=SHIELD_INVOICE_MIN_LEAD_MINUTES,
            ),
            show_alert=True,
        )
        return

    # Экран оплаты. Счёт всегда на ПОЛНУЮ стоимость плёнки: комбинированной
    # оплаты (баланс + карта) в проекте нет, а баланс при внешнем платеже не
    # трогается. Раньше здесь писали «не хватает N ₽», человек жал «Картой» и
    # получал счёт на всю сумму — прямой повод для спора о деньгах.
    text = i18n_get_text(
        language, "farm.shield_payment_title",
        num=plot_id + 1,
        emoji=plant.get("emoji", ""),
        name=_plant_name(language, plot.get("plant_type")),
        price=shield_cost_rub,
        balance=f"{balance / 100:.2f}",
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "farm.pay_card"),
            callback_data=f"farm_shield_lava:{plot_id}",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "farm.pay_sbp"),
            callback_data=f"farm_shield_sbp:{plot_id}",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "farm.back_to_farm"),
            callback_data="game_farm",
        )],
    ])
    try:
        await safe_edit_text(callback.message,text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise

    # Экран показан — гасим «часики». Ответ ровно один: пути с отказом
    # выше уже ответили своим алертом и вышли (см. _render_farm).
    try:
        await callback.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("farm_shield_lava:"))
async def callback_farm_shield_lava(callback: CallbackQuery):
    """Pay shield via Lava (card)."""
    if not await ensure_db_ready_callback(callback):
        return
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    plot_id = _parse_plot_id(callback.data, "farm_shield_lava")
    if plot_id < 0:
        return
    # Экран оплаты живёт в чате и после того, как ситуация изменилась, —
    # проверяем запас времени ещё раз, уже перед выставлением счёта.
    if not await _shield_invoice_allowed(callback, language, telegram_id, plot_id):
        return
    _, _, _, plot = await _find_growing_plot(telegram_id, plot_id)
    if plot is None:
        await callback.answer(
            i18n_get_text(language, "farm.shield_plot_not_growing"), show_alert=True,
        )
        return
    plant = PLANT_TYPES.get(plot.get("plant_type"), {})
    shield_cost = storm_shield_price_kopecks(int(plant.get("reward", 0)))

    import lava_service
    if not lava_service.is_enabled():
        await callback.answer(
            i18n_get_text(language, "farm.pay_card_unavailable"), show_alert=True,
        )
        return

    try:
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff="farm_storm_shield",
            period_days=0,
            price_kopecks=shield_cost,
            purchase_type="farm_effect",
            farm_plot_id=plot_id,
        )
        invoice = await lava_service.create_invoice(
            amount_rubles=shield_cost / 100.0,
            purchase_id=purchase_id,
            comment=i18n_get_text(
                language, "farm.shield_invoice_comment", num=plot_id + 1,
            ),
        )
        invoice_id = invoice["invoice_id"]
        payment_url = invoice["payment_url"]
        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(invoice_id))
        except Exception as e:
            logger.error("Failed to save Lava invoice_id: %s", e)

        text = i18n_get_text(
            language, "farm.shield_lava_invoice", amount=shield_cost // 100,
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "farm.shield_lava_button"), url=payment_url,
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "farm.back_to_farm"), callback_data="game_farm",
            )],
        ])
        await safe_edit_text(callback.message,text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.exception("FARM_SHIELD_LAVA_ERROR user=%s plot=%s: %s", telegram_id, plot_id, e)
        await callback.answer(
            i18n_get_text(language, "farm.pay_error"), show_alert=True,
        )


@router.callback_query(F.data.startswith("farm_shield_sbp:"))
async def callback_farm_shield_sbp(callback: CallbackQuery):
    """Pay shield via Платега (SBP, +11%)."""
    if not await ensure_db_ready_callback(callback):
        return
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    plot_id = _parse_plot_id(callback.data, "farm_shield_sbp")
    if plot_id < 0:
        return
    # См. комментарий в оплате картой: запас времени проверяем и здесь.
    if not await _shield_invoice_allowed(callback, language, telegram_id, plot_id):
        return
    _, _, _, plot = await _find_growing_plot(telegram_id, plot_id)
    if plot is None:
        await callback.answer(
            i18n_get_text(language, "farm.shield_plot_not_growing"), show_alert=True,
        )
        return
    plant = PLANT_TYPES.get(plot.get("plant_type"), {})
    shield_cost = storm_shield_price_kopecks(int(plant.get("reward", 0)))

    import platega_service
    if not platega_service.is_enabled():
        await callback.answer(
            i18n_get_text(language, "farm.pay_sbp_unavailable"), show_alert=True,
        )
        return

    try:
        sbp_kopecks = platega_service.apply_sbp_markup(shield_cost)
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff="farm_storm_shield",
            period_days=0,
            price_kopecks=sbp_kopecks,
            purchase_type="farm_effect",
            farm_plot_id=plot_id,
        )
        tx = await platega_service.create_transaction(
            amount_rubles=sbp_kopecks / 100.0,
            description=i18n_get_text(
                language, "farm.shield_invoice_comment", num=plot_id + 1,
            ),
            purchase_id=purchase_id,
        )
        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(tx["transaction_id"]))
        except Exception as e:
            logger.error("Failed to save SBP tx_id: %s", e)

        text = i18n_get_text(
            language, "farm.shield_sbp_invoice", amount=f"{sbp_kopecks / 100:.2f}",
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "farm.shield_sbp_button"),
                url=tx["redirect_url"],
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "farm.back_to_farm"), callback_data="game_farm",
            )],
        ])
        await safe_edit_text(callback.message,text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.exception("FARM_SHIELD_SBP_ERROR user=%s plot=%s: %s", telegram_id, plot_id, e)
        await callback.answer(
            i18n_get_text(language, "farm.pay_error"), show_alert=True,
        )


@router.callback_query(F.data.startswith("farm_early:"))
async def callback_farm_early_harvest(callback: CallbackQuery):
    """🚜 Собрать незрелым — credits 50% of plant reward, frees the plot."""
    if not await ensure_db_ready_callback(callback):
        return
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    storm = await _get_imminent_storm()
    if storm is None:
        await callback.answer(
            i18n_get_text(language, "farm.early_no_storm"), show_alert=True,
        )
        return

    plot_id = _parse_plot_id(callback.data, "farm_early")
    if plot_id < 0:
        return
    farm_plots, plot_count, balance, plot = await _find_growing_plot(telegram_id, plot_id)
    if plot is None:
        await callback.answer(
            i18n_get_text(language, "farm.shield_plot_not_growing"), show_alert=True,
        )
        return

    plant = PLANT_TYPES.get(plot.get("plant_type"), {})
    half_reward_kopecks = int(plant.get("reward", 0)) // 2
    if half_reward_kopecks <= 0:
        await callback.answer(
            i18n_get_text(language, "farm.early_unavailable"), show_alert=True,
        )
        return

    # Сброс грядки и начисление — одной транзакцией под advisory-локом, иначе
    # двойной клик по «собрать незрелым» начисляет половину награды дважды.
    ok, reason = await database.harvest_plot_atomic(
        telegram_id=telegram_id,
        plot_id=plot_id,
        reward_kopecks=half_reward_kopecks,
        expected_status="growing",
        source="farm_early_harvest",
        description=f"Early harvest plot {plot_id} ({plant.get('name','')})",
    )
    if not ok:
        if reason == "plot_wrong_status":
            await callback.answer(
                i18n_get_text(language, "farm.error_already_harvested"), show_alert=True,
            )
        else:
            logger.warning(
                "FARM_EARLY_HARVEST_FAILED user=%s plot=%s reason=%s",
                telegram_id, plot_id, reason,
            )
            await callback.answer(
                i18n_get_text(language, "farm.early_failed"), show_alert=True,
            )
        return

    await callback.answer(
        i18n_get_text(
            language, "farm.early_success",
            emoji=plant.get("emoji", ""), reward=half_reward_kopecks // 100,
        ),
        show_alert=True,
    )
    pool = await database.get_pool()
    await _render_farm(callback, pool)
