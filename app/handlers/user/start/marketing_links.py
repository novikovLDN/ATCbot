"""Диплинки маркетинга: /start s-<slug> и /start p-<slug>.

ЧТО ЗДЕСЬ
    Две ветки /start и выдача наград по промо-ссылке:

        s-<slug>  только считает клик и атрибуцию, ничего не рисует;
        p-<slug>  выдаёт награду (дни подписки, скидка, ГБ обхода) и сам
                  рисует финальный экран.

ПОЧЕМУ ВЫДЕЛЕНО
    Единственная часть /start, которая раздаёт материальные награды и
    умеет откатывать выдачу. Правится вместе с разделом ссылок в
    дашборде, а не вместе с приветствием.

ЧТО ЛЕГКО СЛОМАТЬ
    Откат. Слот активации резервируется ДО применения награды; если
    применение упало, redemption обязан откатиться — иначе человек
    потерял единственную попытку и не получил ничего.

    Статистическая ссылка не должна прерывать поток и не должна ронять
    /start: любая её ошибка ловится и уходит в лог. Пробросите исключение
    наверх — сломается вход в бота для всех, кто пришёл по ссылке.

    created_by=0 в скидках — маркер «выдано системой». None здесь уже
    ломал выдачу: аннотация int, TypeError, награда «зарезервирована, но
    не применена».
"""
import logging

from aiogram.types import Message
from aiogram.fsm.context import FSMContext

import database
from app.services.language_service import resolve_user_language
from app.handlers.common.keyboards import get_language_keyboard, get_main_menu_keyboard

logger = logging.getLogger(__name__)


async def _handle_stats_link_click(
    telegram_id: int,
    slug: str,
    is_new_user: bool,
) -> None:
    """Записать клик по stat-ссылке. Не рендерит ничего — юзер идёт
    дальше по обычному flow. Ошибки логируются, наверх не пробрасываются."""
    if not slug or len(slug) > 32 or not slug.replace("-", "").isalnum():
        return
    try:
        link = await database.get_stats_link_by_slug(slug)
    except Exception as e:
        logger.warning("STATS_LINK_LOOKUP_FAIL slug=%s err=%s", slug[:16], e)
        return
    if not link or not link.get("is_active"):
        return
    try:
        await database.record_stats_link_click(
            link_id=link["id"],
            telegram_id=telegram_id,
            is_new_user=is_new_user,
        )
        logger.info(
            "STATS_LINK_CLICK slug=%s user=%s new=%s",
            slug[:16], telegram_id, is_new_user,
        )
    except Exception as e:
        logger.warning("STATS_LINK_CLICK_RECORD_FAIL slug=%s err=%s", slug[:16], e)


async def _handle_promo_link_start(
    message: Message,
    state: FSMContext,
    telegram_id: int,
    slug: str,
    is_new_user: bool,
) -> bool:
    """Обработать /start p-<slug>. Возвращает True если рендер прошёл
    и внешний handler НЕ должен продолжать обычный flow.

    Fail-safe: если что-то падает — возвращаем False, юзер получит
    обычное меню, ошибок в чат не бросаем.
    """
    if not slug or len(slug) > 32 or not slug.replace("-", "").isalnum():
        return False

    language = await resolve_user_language(telegram_id)
    try:
        link = await database.get_promo_link_by_slug(slug)
    except Exception as e:
        logger.warning("PROMO_LINK_LOOKUP_FAIL slug=%s err=%s", slug[:16], e)
        return False

    async def _reply(text: str, keyboard=None):
        kb = keyboard
        if kb is None:
            if is_new_user:
                kb = get_language_keyboard(language)
            else:
                kb = await get_main_menu_keyboard(language, telegram_id)
        try:
            await message.answer(text, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            logger.warning("PROMO_LINK_REPLY_FAIL: %s", e)

    if not link:
        await _reply(
            "⚠️ <b>Ссылка не найдена</b>\n\nВозможно, она удалена или "
            "адрес введён неправильно.",
        )
        return True

    try:
        result = await database.try_redeem_promo_link(
            link_id=link["id"],
            telegram_id=telegram_id,
        )
    except Exception as e:
        logger.exception("PROMO_LINK_REDEEM_FAIL slug=%s err=%s", slug[:16], e)
        await _reply("⚠️ <b>Не получилось активировать ссылку</b>\n\nПопробуй ещё раз чуть позже.")
        return True

    if not result.get("ok"):
        reason = result.get("reason", "unknown")
        errors = {
            "inactive": "🚫 <b>Ссылка выключена</b>\n\nАдмин её деактивировал.",
            "expired": "⏳ <b>Срок действия ссылки истёк</b>",
            "exhausted": "🚫 <b>Ссылка полностью использована</b>\n\nЛимит активаций исчерпан.",
            "already_redeemed_by_user": "ℹ️ <b>Ты уже использовал эту ссылку</b>\n\nОдна активация на пользователя.",
            "not_found": "⚠️ <b>Ссылка не найдена</b>",
            "db_not_ready": "⚠️ <b>Сервис перезапускается</b>\n\nПопробуй через минуту.",
        }
        await _reply(errors.get(reason, "⚠️ <b>Активация не прошла</b>"))
        return True

    # Всё ок — награда зарезервирована, применяем её.
    reward_type = result["reward_type"]
    reward_value = int(result["reward_value"])
    reward_meta = result.get("reward_meta") or {}

    try:
        applied_ok, applied_text = await _apply_promo_reward(
            telegram_id, reward_type, reward_value, reward_meta,
        )
    except Exception as e:
        logger.exception(
            "PROMO_LINK_APPLY_FAIL user=%s slug=%s type=%s err=%s",
            telegram_id, slug[:16], reward_type, e,
        )
        applied_ok = False
        applied_text = ""

    if not applied_ok:
        # Откатываем редемпцию, чтобы юзер не потерял слот навсегда:
        # снимаем запись из promo_link_redemptions + декрементим
        # used_count. Игнорируем ошибку rollback'а — если сюда упало,
        # хуже уже не будет.
        try:
            await database.rollback_promo_link_redemption(link["id"], telegram_id)
        except Exception as e:
            logger.warning(
                "PROMO_LINK_ROLLBACK_FAIL slug=%s user=%s err=%s",
                slug[:16], telegram_id, e,
            )
        await _reply(
            "⚠️ <b>Награда пока не применилась</b>\n\n"
            "Попробуй ещё раз через минуту или напиши в поддержку — "
            "мы всё выдадим.",
        )
        return True

    # Финализация. Для скидочных наград сразу открываем экран выбора
    # тарифа (там уже применена скидка автоматически). Для остальных
    # (subscription_days, bypass_gb) — просто главное меню, у юзера
    # уже есть подписка/ГБ, ему нужен доступ к «Подключиться».
    logger.info(
        "PROMO_LINK_ACTIVATED user=%s slug=%s type=%s value=%s",
        telegram_id, slug[:16], reward_type, reward_value,
    )

    goes_to_tariffs = reward_type in ("tariff_discount", "bypass_discount")

    if goes_to_tariffs:
        # Success-сообщение без клавиатуры — сразу под ним появится
        # экран выбора тарифа с уже применённой скидкой.
        try:
            await message.answer(applied_text, parse_mode="HTML")
        except Exception as e:
            logger.warning("PROMO_LINK_SUCCESS_MSG_FAIL: %s", e)
        try:
            # from_broadcast=True: чтобы «Назад» с экрана периода вела
            # обратно на экран тарифов, а не на «Управление подпиской».
            # Тот же паттерн, что и в gift_reveal-handler'е.
            await state.update_data(from_broadcast=True)
            from app.handlers.common.screens import show_tariffs_main_screen
            await show_tariffs_main_screen(message, state, force_new_message=True)
        except Exception as e:
            logger.exception("PROMO_LINK_OPEN_TARIFFS_FAIL: %s", e)
            # Fallback — покажем главное меню, чтоб юзер не остался
            # с висящим успехом без CTA.
            fallback_kb = (
                get_language_keyboard(language) if is_new_user
                else await get_main_menu_keyboard(language, telegram_id)
            )
            await _reply(
                "Открой «Купить подписку» — скидка применится автоматически.",
                keyboard=fallback_kb,
            )
        return True

    # Остальные типы (subscription_days, bypass_gb) — обычное меню.
    keyboard = (
        get_language_keyboard(language) if is_new_user
        else await get_main_menu_keyboard(language, telegram_id)
    )
    header = "🎉 <b>Награда активирована!</b>\n\n"
    await _reply(header + applied_text, keyboard=keyboard)
    return True


async def _apply_promo_reward(
    telegram_id: int,
    reward_type: str,
    reward_value: int,
    reward_meta: dict,
) -> tuple[bool, str]:
    """Применить награду. Возвращает (ok, user_facing_text).

    Реализовано через существующие database helper'ы: grant_access,
    create_user_discount, create_user_traffic_discount, add_bypass_traffic.
    """
    from datetime import datetime, timedelta, timezone

    if reward_type == "subscription_days":
        days = int(reward_value)
        tariff = str(reward_meta.get("tariff") or "basic").lower()
        if tariff not in ("basic", "plus"):
            tariff = "basic"
        try:
            # source="admin" — валидное значение, весь branch-код в
            # grant_access его знает (avoiding нестандартный "promo_link",
            # который мог бы пойти по неожиданной ветке в renewal-логике).
            res = await database.grant_access(
                telegram_id=telegram_id,
                duration=timedelta(days=days),
                source="admin",
                admin_telegram_id=None,
                admin_grant_days=days,
                tariff=tariff,
            )
        except Exception as e:
            logger.exception("PROMO_APPLY_SUBSCRIPTION_FAIL: %s", e)
            return False, ""
        end = res.get("subscription_end")
        end_str = end.strftime("%d.%m.%Y") if end else "—"
        return True, (
            f"📦 <b>Подписка</b> · {tariff.capitalize()}\n"
            f"⏳ <b>{days} дн.</b>\n"
            f"📅 До: <b>{end_str}</b>"
        )

    if reward_type == "tariff_discount":
        hours = int(reward_meta.get("hours") or 24)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)
        try:
            # created_by = 0 — маркер «promo-link / system», как для
            # bypass ниже. Раньше стоял None → TypeError из int-annotation,
            # скидка не создавалась, юзер видел «награда зарезервирована,
            # но применить не получилось», при этом redemption уже
            # инкрементилась (роллбэк добавлен в try_redeem_promo_link
            # ниже).
            ok = await database.create_user_discount(
                telegram_id=telegram_id,
                discount_percent=int(reward_value),
                expires_at=expires_at,
                created_by=0,
            )
        except Exception as e:
            logger.exception("PROMO_APPLY_TARIFF_DISC_FAIL: %s", e)
            return False, ""
        if not ok:
            return False, ""
        return True, (
            "🎁 <b>Твой подарок активирован</b>\n\n"
            f"<blockquote>— Скидка <b>{reward_value}%</b> на любой тариф\n"
            f"— Действует ещё <b>{hours} часов</b></blockquote>\n\n"
            "Выбери подходящий тариф ниже ↓"
        )

    if reward_type == "bypass_discount":
        hours = int(reward_meta.get("hours") or 24)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)
        try:
            await database.create_user_traffic_discount(
                telegram_id=telegram_id,
                discount_percent=int(reward_value),
                expires_at=expires_at,
                created_by=0,
            )
        except Exception as e:
            logger.exception("PROMO_APPLY_BYPASS_DISC_FAIL: %s", e)
            return False, ""
        return True, (
            "🎁 <b>Твой подарок активирован</b>\n\n"
            f"<blockquote>— Скидка <b>{reward_value}%</b> на пакеты ГБ обхода\n"
            f"— Действует ещё <b>{hours} часов</b></blockquote>\n\n"
            "Выбери подходящий тариф ниже ↓"
        )

    if reward_type == "bypass_gb":
        gb = int(reward_value)
        extra_bytes = gb * 1024 * 1024 * 1024
        try:
            existing = await database.get_subscription(telegram_id)
            if not existing:
                try:
                    await database.ensure_bypass_only_subscription(telegram_id)
                except Exception as e:
                    logger.warning("PROMO_ENSURE_BYPASS_ONLY_FAIL: %s", e)
            from app.services.remnawave_service import add_bypass_traffic
            granted = await add_bypass_traffic(
                telegram_id=telegram_id,
                extra_bytes=extra_bytes,
                subscription_type="basic",
                subscription_end=None,
                period_days=30,
            )
        except Exception as e:
            logger.exception("PROMO_APPLY_BYPASS_GB_FAIL: %s", e)
            return False, ""
        if not granted:
            return False, ""
        return True, (
            f"📊 <b>+{gb} ГБ</b> обхода начислено\n\n"
            "Пакет ГБ не сгорает — тратится только при работе на LTE-серверах."
        )

    return False, ""
