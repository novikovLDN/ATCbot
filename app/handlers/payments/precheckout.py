"""Подтверждение платежа Telegram перед списанием (pre_checkout_query).

ЧТО ЗДЕСЬ
    Один обработчик. Telegram спрашивает бота «пропускать ли платёж» и ждёт
    ответа не дольше 10 секунд; не ответили — человек видит ошибку оплаты и
    деньги не списываются.

ПОЧЕМУ ОТДЕЛЬНЫМ МОДУЛЕМ
    Это единственная точка бота, работающая под жёстким таймаутом, и правят
    её по своим поводам (сроки жизни счёта, поведение при недоступной базе).
    В payments_messages.py она лежала рядом с обработчиком успешной оплаты
    на тысячу строк и терялась.

ЧТО ЛЕГКО СЛОМАТЬ
    Любая новая проверка здесь — это лишние миллисекунды под таймаутом.
    И помните про ветку «база недоступна»: она осознанно ПРОПУСКАЕТ платёж,
    поэтому successful_payment обязан уметь пережить ненайденную покупку.
"""
import logging

from aiogram import Router
from aiogram.types import PreCheckoutQuery

import database

precheckout_router = Router()
logger = logging.getLogger(__name__)


@precheckout_router.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    """Подтверждение платежа перед списанием. КРИТИЧНО: ответить в течение таймаута Telegram (10 сек)."""
    payload = pre_checkout_query.invoice_payload or ""
    telegram_id = pre_checkout_query.from_user.id if pre_checkout_query.from_user else 0
    is_stars = (pre_checkout_query.currency == "XTR")
    log_amount = pre_checkout_query.total_amount if is_stars else (pre_checkout_query.total_amount / 100 if pre_checkout_query.total_amount else 0)

    # Валидация purchase payload — отклоняем если pending_purchase истёк или не найден
    if payload.startswith("purchase:"):
        purchase_id = payload.split(":", 1)[1]
        try:
            pending = await database.get_pending_purchase(purchase_id, telegram_id, check_expiry=True)
            if not pending:
                logger.warning(
                    "PRE_CHECKOUT_REJECTED purchase_id=%s telegram_id=%s reason=expired_or_not_found",
                    purchase_id, telegram_id,
                )
                await pre_checkout_query.answer(ok=False, error_message="Invoice expired. Please create a new one.")
                return
        except Exception as e:
            # Осознанный компромисс: при недоступности базы платёж НЕ блокируем.
            #
            # Telegram даёт на ответ 10 секунд, и отказ означает, что человек
            # не сможет заплатить вообще. Пропустить платёж и разобраться позже
            # дешевле, чем потерять покупку из-за временного сбоя базы.
            #
            # Обратная сторона: successful_payment может не найти покупку, и
            # тогда деньги списаны, а товар не выдан. Поэтому уровень critical
            # и отдельный маркер — такие случаи обязаны попадать в разбор,
            # а не теряться среди обычных ошибок.
            logger.critical(
                "PRE_CHECKOUT_DB_ERROR_ALLOWED purchase_id=%s telegram_id=%s error=%s "
                "— платёж пропущен без проверки покупки, проверьте выдачу вручную",
                purchase_id, telegram_id, e,
            )
            try:
                from app.services.admin_alerts import alert_payment_failure
                await alert_payment_failure(
                    pre_checkout_query.bot, "telegram_payment", telegram_id,
                    purchase_id, e, is_transient=True,
                    amount_rubles=log_amount, tariff=None, period_days=None,
                )
            except Exception as alert_err:
                logger.error("PRE_CHECKOUT_ALERT_FAILED: %s", alert_err)
    else:
        purchase_id = payload

    await pre_checkout_query.answer(ok=True)
    logger.info(
        "PRE_CHECKOUT_APPROVED purchase_id=%s telegram_id=%s amount=%s %s",
        purchase_id,
        telegram_id,
        log_amount,
        "XTR" if is_stars else "RUB",
    )
