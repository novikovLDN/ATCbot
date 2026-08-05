"""Гигабайты обхода за комбо-тариф — единственное место, где они начисляются.

ЧТО ЗДЕСЬ
    grant_combo_traffic — начислить пакет ГБ, который входит в комбо-тариф,
    записать покупку трафика и пометить подписку комбо. Всё остальное
    (деньги, выдача самой подписки, экран успеха) делают вызывающие.

ПОЧЕМУ ОДНА ФУНКЦИЯ
    Начисление было размножено на четыре независимые копии: Telegram-инвойс
    и Stars, вебхуки Platega/CryptoBot/Lava, оплата с баланса, админская
    выдача. Копии разъезжались — правка в одной не доезжала до остальных, и
    заметить расхождение можно было только по жалобе человека, который
    заплатил за комбо и получил подписку без гигабайтов.

ЧТО ЛЕГКО СЛОМАТЬ
    Объём ГБ берётся ТОЛЬКО из config.COMBO_TARIFFS (через
    app/constants/tariffs.combo_bypass_gb). Никаких значений из FSM: FSM
    живёт в памяти и между выставлением счёта и оплатой теряется — бот
    перезапустился или человек открыл другое меню, и объём брать неоткуда.
    Вернёте FSM как источник объёма — вернёте дефект.

    Функция НЕ бросает исключений: на всех путях деньги уже взяты, а
    подписка уже выдана, и падение здесь оставило бы покупателя без экрана
    успеха. Решение «просить провайдера повторить платёж или нет» принимает
    вызывающий по полю granted: на вебхуках отказ обязан подниматься как
    TransientPaymentError, в интерактивных путях — нет.

    Порядок записей в лог: «начислено» пишется ПОСЛЕ успеха панели, отказ —
    уровнем error, потому что человек заплатил и не получил товар. В обеих
    записях есть telegram_id и purchase_id — без них цепочку разбора не
    собрать.
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import database
from app.constants import tariffs as tariff_ref

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ComboTrafficOutcome:
    """Чем кончилось начисление.

    granted — гигабайты в панели, всё остальное уже неважно.
    gb      — сколько ГБ полагалось (не ноль даже при отказе: по этому
              числу админ доначисляет вручную).
    reason  — granted | not_combo | no_gb_in_config | panel_rejected | error.
              Вызывающий смотрит на него, чтобы решить, временный это отказ
              (стоит повторить платёж) или постоянный.
    """

    granted: bool
    gb: int
    reason: str


async def grant_combo_traffic(
    telegram_id: int,
    tariff_type: Optional[str],
    period_days: Optional[int],
    *,
    is_combo: bool,
    purchase_id: Any = None,
    subscription_end: Optional[datetime] = None,
    source: str = "",
) -> ComboTrafficOutcome:
    """Начислить ГБ обхода за комбо-тариф.

    tariff_type принимается в любом виде: и базовый ('plus'), как он лежит
    в pending_purchases и subscriptions, и полный ('combo_plus'), как его
    называет админская выдача. Комбо не хранится отдельным типом подписки,
    поэтому один и тот же продукт приходит сюда под двумя именами.

    source — путь оплаты для лога ('balance', 'webhook:platega', ...).
    """
    if not is_combo:
        return ComboTrafficOutcome(False, 0, "not_combo")

    base = (tariff_type or "basic").strip().lower()
    combo_key = base if tariff_ref.is_combo_tariff(base) else f"{tariff_ref.COMBO_PREFIX}{base}"
    gb = tariff_ref.combo_bypass_gb(combo_key, period_days)
    if gb <= 0:
        logger.error(
            "COMBO_TRAFFIC_NO_CONFIG source=%s user=%s purchase_id=%s combo=%s period_days=%s "
            "— оплачено комбо, но пакета ГБ для такого срока в COMBO_TARIFFS нет; "
            "начислить нечего, нужен ручной разбор",
            source or "?", telegram_id, purchase_id, combo_key, period_days,
        )
        return ComboTrafficOutcome(False, 0, "no_gb_in_config")

    from app.services.remnawave_service import add_bypass_traffic

    try:
        ok = await add_bypass_traffic(
            telegram_id,
            gb * 1024 ** 3,
            subscription_type=tariff_ref.base_tariff_of(combo_key),
            subscription_end=subscription_end,
            period_days=period_days or 30,
        )
    except Exception as err:
        logger.error(
            "COMBO_TRAFFIC_ERROR source=%s user=%s purchase_id=%s gb=%s error=%s: %s "
            "— оплачено комбо, гигабайты НЕ начислены",
            source or "?", telegram_id, purchase_id, gb, type(err).__name__, err,
        )
        return ComboTrafficOutcome(False, gb, "error")

    if not ok:
        logger.error(
            "COMBO_TRAFFIC_FAILED source=%s user=%s purchase_id=%s gb=%s "
            "— панель не приняла начисление; оплачено комбо, гигабайты НЕ начислены",
            source or "?", telegram_id, purchase_id, gb,
        )
        return ComboTrafficOutcome(False, gb, "panel_rejected")

    # Дальше только учёт: гигабайты уже в панели. Ни одна ошибка ниже не
    # должна превращать состоявшееся начисление в отказ — иначе вызывающий
    # попросит повторить платёж и человек получит пакет второй раз.
    try:
        await database.record_traffic_purchase(telegram_id, gb, 0)
    except Exception as err:
        logger.warning(
            "COMBO_TRAFFIC_RECORD_FAIL user=%s purchase_id=%s gb=%s: %s "
            "— ГБ начислены, в статистике покупок трафика их не будет",
            telegram_id, purchase_id, gb, err,
        )
    try:
        await database.set_combo_flag(telegram_id, True)
    except Exception as err:
        logger.warning(
            "COMBO_TRAFFIC_FLAG_FAIL user=%s purchase_id=%s: %s "
            "— ГБ начислены, но подписка не помечена комбо: автопродление "
            "спишет цену обычного тарифа",
            telegram_id, purchase_id, err,
        )

    logger.info(
        "COMBO_TRAFFIC_GRANTED source=%s user=%s purchase_id=%s combo=%s period_days=%s gb=%s",
        source or "?", telegram_id, purchase_id, combo_key, period_days, gb,
    )
    return ComboTrafficOutcome(True, gb, "granted")
