"""Диплинк «подари другу скидку»: /start refd_<код>.

ЧТО ЗДЕСЬ
    Одна ветка /start: получатель ссылки получает 30% на 24 часа, а если
    он новый — ему заодно закрепляется пригласивший.

ПОЧЕМУ ВЫДЕЛЕНО
    Своя механика и свой набор правил, к остальным диплинкам отношения
    не имеющий. В общем файле она тонула среди подарков и промо-ссылок.

ЧТО ЛЕГКО СЛОМАТЬ
    Скидка даётся один раз за всю жизнь аккаунта, и это проверяется
    дважды: до вставки (has_claimed…) и по результату вставки (record…
    вернул 0 — значит гонку выиграл кто-то другой). Убрать вторую
    проверку — и параллельные клики раздадут скидку повторно.

    Существующая скидка ≥30% НЕ перезаписывается: create_user_discount
    делает ON CONFLICT DO UPDATE безусловно, поэтому сравнение здесь —
    единственное, что мешает ухудшить условия человеку.

    Возврат True означает «экран отрисован полностью, /start должен
    выйти». Вернёте False после ответа пользователю — он получит два
    экрана подряд.
"""
import logging
from datetime import datetime, timezone

from aiogram.types import Message
from aiogram.fsm.context import FSMContext

import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.keyboards import get_main_menu_keyboard

logger = logging.getLogger(__name__)


_SHARE_DISCOUNT_PERCENT = 30
_SHARE_DISCOUNT_HOURS = 24


async def _handle_share_discount_start(
    message: Message,
    state: FSMContext,
    telegram_id: int,
    refd_code: str,
    is_new_user: bool,
) -> bool:
    """Process /start refd_<code> — share-discount activation.

    Возвращает True, если экран отрендерен полностью и cmd_start должен
    выйти. False — продолжаем стандартный flow (например, payload
    оказался кривой и мы хотим показать обычное приветствие).

    Семантика:
      • self-referral → блок + main-меню (нечего здесь покупать)
      • lifetime claim уже есть → notice + экран тарифов (юзер всё
        равно мог прийти выбирать тариф; если активная скидка ещё
        жива — увидит её на экране автоматически)
      • новый юзер → закрепить referrer_id через стандартный pipeline
        (process_referral_registration с конвертацией refd_→ref_)
      • выдать 30% / 24ч personal discount (если нет более выгодной)
      • записать в referral_share_discount_claims
      • показать notice + экран тарифов (скидка автоматически
        отрисуется в ценах — _open_buy_screen зовёт get_user_discount)
    """
    from datetime import timedelta
    from app.services.referrals import process_referral_registration
    from app.handlers.common.screens import show_tariffs_main_screen

    # Sanity: код — alphanumeric, 4–12 символов (наш формат 6).
    if not refd_code or len(refd_code) > 32 or not refd_code.replace("_", "").isalnum():
        logger.warning(
            "REFDC_INVALID_PAYLOAD user=%s code=%s",
            telegram_id, refd_code[:30],
        )
        return False  # fall through to normal /start

    language = await resolve_user_language(telegram_id)

    # Найти владельца кода. Сначала opaque referral_code, затем legacy
    # numeric telegram_id (та же логика, что в process_referral_registration).
    referrer_user = await database.find_user_by_referral_code(refd_code)
    referrer_id: int | None = None
    if referrer_user:
        referrer_id = referrer_user.get("telegram_id")
    else:
        try:
            maybe = int(refd_code)
            legacy = await database.get_user(maybe)
            if legacy:
                referrer_id = maybe
        except (ValueError, TypeError):
            pass

    if referrer_id is None:
        logger.warning(
            "REFDC_UNKNOWN_CODE user=%s code=%s — falling back to normal /start",
            telegram_id, refd_code[:30],
        )
        return False

    # Self-referral block — main-меню, чтобы не подталкивать к покупке
    # через манипуляцию собственной ссылкой.
    if referrer_id == telegram_id:
        logger.info("REFDC_SELF_BLOCKED user=%s", telegram_id)
        text = i18n_get_text(language, "share_discount.self_blocked")
        keyboard = await get_main_menu_keyboard(language, telegram_id)
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        return True

    # Lifetime-once guard. Покажем notice отдельным сообщением, потом
    # отрисуем экран тарифов — юзер пришёл сюда явно за подпиской.
    if await database.has_claimed_referral_share_discount(telegram_id):
        logger.info("REFDC_ALREADY_CLAIMED user=%s", telegram_id)
        await message.answer(
            i18n_get_text(language, "share_discount.already_claimed"),
            parse_mode="HTML",
        )
        await show_tariffs_main_screen(message, state)
        return True

    # Новый юзер → закрепить referrer_id через стандартный пайплайн.
    # Конвертируем refd_<code> → ref_<code>, чтобы переиспользовать
    # validation/loop-detection/audit, который уже отлажен.
    if is_new_user:
        try:
            await process_referral_registration(telegram_id, f"ref_{refd_code}")
        except Exception:
            logger.exception("REFDC_REFERRAL_REGISTRATION_FAIL user=%s", telegram_id)

    # Выдать personal-discount. Если у юзера уже есть скидка ≥30% —
    # не перезаписываем, оставляем выгоднее. create_user_discount
    # делает ON CONFLICT DO UPDATE безусловно, поэтому проверяем сами.
    expires_at = datetime.now(timezone.utc) + timedelta(hours=_SHARE_DISCOUNT_HOURS)
    existing = await database.get_user_discount(telegram_id)
    keep_existing = bool(
        existing and existing.get("discount_percent", 0) >= _SHARE_DISCOUNT_PERCENT
    )
    if not keep_existing:
        try:
            await database.create_user_discount(
                telegram_id=telegram_id,
                discount_percent=_SHARE_DISCOUNT_PERCENT,
                expires_at=expires_at,
                created_by=referrer_id,
            )
        except Exception:
            logger.exception("REFDC_DISCOUNT_CREATE_FAIL user=%s", telegram_id)
            # Не критично — продолжаем, claim всё равно фиксируем чтобы
            # юзер не мог попытаться снова и снова.

    recorded = await database.record_referral_share_discount_claim(
        telegram_id=telegram_id,
        referrer_id=referrer_id,
        discount_percent=_SHARE_DISCOUNT_PERCENT,
        duration_hours=_SHARE_DISCOUNT_HOURS,
        expires_at=expires_at,
    )
    if not recorded:
        # Race-condition: между нашим has_claimed-чеком и INSERT'ом
        # успели вставить параллельным процессом. Покажем notice +
        # тарифы (скидка от первого «победителя» уже в DB).
        logger.info("REFDC_RACE_LOST user=%s — claim insert returned 0", telegram_id)
        await message.answer(
            i18n_get_text(language, "share_discount.already_claimed"),
            parse_mode="HTML",
        )
        await show_tariffs_main_screen(message, state)
        return True

    logger.info(
        "REFDC_CLAIMED user=%s referrer=%s pct=%s hours=%s",
        telegram_id, referrer_id, _SHARE_DISCOUNT_PERCENT, _SHARE_DISCOUNT_HOURS,
    )

    # Notice об активации + экран тарифов. _open_buy_screen внутри
    # show_tariffs_main_screen сам подтянет get_user_discount и
    # отрисует уже скидочные цены — двойной работы нет.
    await message.answer(
        i18n_get_text(language, "share_discount.activated"),
        parse_mode="HTML",
    )
    await show_tariffs_main_screen(message, state)
    return True
