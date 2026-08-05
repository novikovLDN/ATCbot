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

ПОВТОР И КЛЮЧ ИДЕМПОТЕНТНОСТИ
    add_bypass_traffic ПРИБАВЛЯЕТ гигабайты к лимиту в панели. Второй вызов
    выдаёт второй пакет, и увидеть это некому: лишние ГБ в поддержку не
    приносят. Поэтому у путей, которые умеют повторяться (вебхуки
    провайдеров и воркер отложенной активации), есть idempotency_key —
    идентификатор ОПЛАЧЕННОЙ ПОКУПКИ. Он ложится в
    traffic_purchases.purchase_id под частичным уникальным индексом
    (миграция 075) и проверяется здесь перед начислением.

    Ключ передают не все. Он обязан быть уникальным на покупку, иначе
    защита превращается в отказ выдать законный второй пакет: у админской
    выдачи метка purchase_id='admin_grant_<tg>' повторяется при каждой
    выдаче тому же человеку, у отложенной активации 'subscription_<id>' —
    при каждом продлении. Такие метки годятся для лога и не годятся для
    ключа, поэтому в тех путях purchase_id остаётся только меткой.

    Проверка ключа — fail-closed: если узнать, начислялись ли ГБ, не
    удалось, мы НЕ начисляем. Отказ виден (лог, payment_errors, повтор
    вебхука), а лишний пакет не виден никому.
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

    granted — гигабайты у человека есть. Именно так, а не «мы их сейчас
              выдали»: при повторе они уже выданы прошлым вызовом, и
              вызывающему нужно знать про итог, а не про свою роль в нём.
              По этому полю вебхук решает, просить провайдера повторить.
    gb      — сколько ГБ полагалось (не ноль даже при отказе: по этому
              числу админ доначисляет вручную).
    reason  — granted | already_granted | not_combo | no_gb_in_config |
              panel_rejected | error | idempotency_unknown. Вызывающий
              смотрит на него, чтобы решить, временный это отказ (стоит
              повторить платёж) или постоянный.
    already_granted — за эту покупку ГБ начислял кто-то до нас. Отдельным
              полем, а не только строкой в reason: молчаливое «всё хорошо»
              здесь неотличимо от настоящей выдачи, а разница между ними —
              это разница между одним пакетом и двумя.
    """

    granted: bool
    gb: int
    reason: str
    already_granted: bool = False


async def grant_combo_traffic(
    telegram_id: int,
    tariff_type: Optional[str],
    period_days: Optional[int],
    *,
    is_combo: bool,
    purchase_id: Any = None,
    idempotency_key: Optional[str] = None,
    subscription_end: Optional[datetime] = None,
    source: str = "",
) -> ComboTrafficOutcome:
    """Начислить ГБ обхода за комбо-тариф.

    tariff_type принимается в любом виде: и базовый ('plus'), как он лежит
    в pending_purchases и subscriptions, и полный ('combo_plus'), как его
    называет админская выдача. Комбо не хранится отдельным типом подписки,
    поэтому один и тот же продукт приходит сюда под двумя именами.

    purchase_id — метка для лога и алертов, может быть любой.
    idempotency_key — ключ «за эту покупку уже начислено». Передавать его
    можно ТОЛЬКО если он уникален на покупку (см. шапку модуля): вебхуки
    передают purchase_id из pending_purchases, воркер активации — его же,
    подтянутый из оплаченной покупки. Без ключа защиты от второго пакета
    нет — и это осознанно: путь без повторов её и не требует.

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
        await _record_delivery_failure(
            telegram_id, purchase_id, 0, "no_gb_in_config", source,
        )
        return ComboTrafficOutcome(False, 0, "no_gb_in_config")

    # Ключ идемпотентности — перед панелью, потому что панель не умеет
    # сказать «этот пакет я уже добавляла»: add_bypass_traffic просто
    # прибавит вторые гигабайты.
    if idempotency_key:
        already = await database.combo_traffic_already_granted(str(idempotency_key))
        if already:
            logger.info(
                "COMBO_TRAFFIC_ALREADY_GRANTED source=%s user=%s purchase_id=%s key=%s gb=%s "
                "— за эту покупку гигабайты уже начислены, второй пакет не выдаём",
                source or "?", telegram_id, purchase_id, idempotency_key, gb,
            )
            return ComboTrafficOutcome(True, gb, "already_granted", already_granted=True)
        if already is None:
            # Fail-closed. Не начислить — обратимо: вебхук ответит 5xx и
            # провайдер повторит, а строка в payment_errors соберёт
            # пострадавших списком. Начислить вслепую — необратимо.
            logger.error(
                "COMBO_TRAFFIC_KEY_UNKNOWN source=%s user=%s purchase_id=%s key=%s gb=%s "
                "— проверить ключ идемпотентности не удалось; начисление отложено, "
                "чтобы не выдать второй пакет",
                source or "?", telegram_id, purchase_id, idempotency_key, gb,
            )
            await _record_delivery_failure(
                telegram_id, purchase_id, gb, "idempotency_unknown", source,
            )
            return ComboTrafficOutcome(False, gb, "idempotency_unknown")

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
        await _record_delivery_failure(
            telegram_id, purchase_id, gb, "error", source, f"{type(err).__name__}: {err}",
        )
        return ComboTrafficOutcome(False, gb, "error")

    if not ok:
        logger.error(
            "COMBO_TRAFFIC_FAILED source=%s user=%s purchase_id=%s gb=%s "
            "— панель не приняла начисление; оплачено комбо, гигабайты НЕ начислены",
            source or "?", telegram_id, purchase_id, gb,
        )
        await _record_delivery_failure(
            telegram_id, purchase_id, gb, "panel_rejected", source,
        )
        return ComboTrafficOutcome(False, gb, "panel_rejected")

    # Дальше только учёт: гигабайты уже в панели. Ни одна ошибка ниже не
    # должна превращать состоявшееся начисление в отказ — иначе вызывающий
    # попросит повторить платёж и человек получит пакет второй раз.
    try:
        await database.record_traffic_purchase(
            telegram_id, gb, 0, purchase_id=str(idempotency_key) if idempotency_key else None,
        )
    except Exception as err:
        # Уровень error, а не warning: вместе со статистикой не записался
        # ключ идемпотентности. Гигабайты выданы, но следующий повтор
        # вебхука этого не увидит и выдаст второй пакет — единственное
        # оставшееся окно для двойного начисления, и оно должно быть
        # заметным.
        logger.error(
            "COMBO_TRAFFIC_RECORD_FAIL user=%s purchase_id=%s key=%s gb=%s: %s "
            "— ГБ начислены, но покупка не записана; ключ идемпотентности не "
            "сохранён, повтор вебхука может выдать второй пакет",
            telegram_id, purchase_id, idempotency_key, gb, err,
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


async def _record_delivery_failure(
    telegram_id: int,
    purchase_id: Any,
    gb: int,
    reason: str,
    source: str,
    detail: str = "",
) -> None:
    """Положить невыданный пакет в payment_errors — туда, где его видно списком.

    Зачем, если отказ уже в логе. Лог годится, когда знаешь, что искать:
    админ идёт в него по конкретной жалобе. Пострадавших целиком он не
    даёт — их надо суметь перечислить, а не находить по одному. Строка в
    payment_errors попадает на страницу платежей в дашборде вместе с
    telegram_id и purchase_id, и по stage их видно списком.

    Это нужнее всего там, где отказ никуда не поднимается: Telegram-инвойс
    и Stars деньги уже взяли и экран успеха уже показали, повторять платёж
    некому. Единственный след — вот этот.

    Никогда не бросает и не задерживает вызывающего: это уже обработка
    отказа, вторая ошибка поверх первой ничего не улучшит. Вебхук платит
    за неё одним INSERT и только на пути отказа.
    """
    try:
        await database.log_payment_error(
            stage="combo_traffic_not_granted",
            telegram_id=telegram_id,
            purchase_id=str(purchase_id) if purchase_id is not None else None,
            payment_provider=source or None,
            error_code=reason,
            error_message=(
                f"комбо оплачено, {gb} ГБ обхода не начислены"
                + (f": {detail}" if detail else "")
            ),
        )
    except Exception as err:
        logger.warning(
            "COMBO_TRAFFIC_ERROR_LOG_FAIL user=%s purchase_id=%s reason=%s: %s "
            "— отказ остался только в логе приложения",
            telegram_id, purchase_id, reason, err,
        )
