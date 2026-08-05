"""Правила фермы: время роста, ускорение уходом и окно шторма.

ЧТО ЗДЕСЬ
    Всё, что можно посчитать без Telegram: название культуры на языке
    игрока, потолок ускорения от полива и удобрения, признак «шторм уже
    объявлен» и запас времени до удара.

ПОЧЕМУ ОТДЕЛЬНО ОТ ОБРАБОТЧИКОВ
    Эти правила зовут из трёх мест — экран (screen.py), грядки (plots.py) и
    шторм (storm.py). Пока они лежали среди обработчиков, менять формулу
    ускорения означало открывать файл на 1386 строк и читать посадку, оплату
    плёнки и три экрана подтверждения.

ЧТО ЛЕГКО СЛОМАТЬ
    _apply_growth_boost двигает ready_at и dead_at на ОДНУ И ТУ ЖЕ величину.
    Подвинуть только ready_at — значит растянуть окно сбора с положенных
    24 часов на всё накопленное ускорение.

    STORM_STALE_AFTER_HOURS — предохранитель, а не косметика. Пока шторм
    «объявлен и не исполнен», посадка выключена у всех; упавший воркер без
    этого окна оставляет ферму мёртвой навсегда и без единой ошибки в
    интерфейсе.
"""
import logging
from datetime import datetime, timedelta, timezone

import database
from app.i18n import get_text as i18n_get_text

# Общие с игровым меню элементы импортируются из game.py: справочник
# растений. Держать его там правильно — меню игр живёт в game.py.
from app.handlers.game import PLANT_TYPES

logger = logging.getLogger(__name__)


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
