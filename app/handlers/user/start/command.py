"""Команда /start: разбор диплинка и вход в бота.

ЧТО ЗДЕСЬ
    Одна функция — cmd_start. Она большая, потому что /start это точка
    входа для СЕМИ разных ссылок, и порядок их проверки — часть
    поведения:

        <token>        привязка аккаунта сайта (не прерывает поток)
        bgift_<код>    подарочные гигабайты обхода   → выходит
        gift_<код>     активация подарочной подписки → выходит
        s-<slug>       клик по статистической ссылке (не прерывает)
        p-<slug>       промо-ссылка с наградой       → может выйти
        refd_<код>     скидка «подари другу»         → может выйти
        ref_<код>      обычная реферальная регистрация

    Дальше — приветствие и выбор языка.

ПОЧЕМУ ФУНКЦИЯ НЕ РАЗРЕЗАНА
    Ветки делят общее состояние (user, is_new_user, start_language,
    telegram_id) и по-разному решают, прерывать ли поток. Разложить их по
    функциям — это правка поведения, а не перенос: любая перепутанная
    ветка = человек по ссылке из рассылки не получает то, за чем пришёл,
    и об этом не будет ни строки в логах. Такую правку надо делать
    отдельно и под тесты на каждый вид ссылки.

ЧТО ЛЕГКО СЛОМАТЬ
    Порядок проверок. `refd_` разбирается ДО `ref_`: префиксы не
    пересекаются по startswith, но перепутанный порядок сделает скидку
    «подари другу» обычной регистрацией.

    Коды подарков — предъявительские: кто прочитал код, тот и заберёт
    чужую оплаченную подписку или гигабайты. В логи идёт только маска
    (mask_secret), цепочка собирается по telegram_id.

    Ветка bgift зовёт ensure_bypass_only_subscription ТОЛЬКО когда
    активной подписки нет: иначе она затрёт срок действующей подписки
    датой +10 лет.

    Проверка DB_READY в начале — деградированный режим. Без базы бот
    обязан показать меню, а не молчать.
"""
import logging
from typing import Optional

import database
import config
from aiogram import Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.utils.referral_middleware import process_referral_on_first_interaction
from app.handlers.common.keyboards import get_language_keyboard, get_main_menu_keyboard
from app.handlers.common.utils import safe_resolve_username
# Коды подарков (gift_) и бонусных ссылок на ГБ (bgift_) — предъявительские
# токены: кто прочитал код, тот и активирует чужую оплаченную подписку или
# забирает гигабайты. У bgift-ссылок код к тому же многоразовый (есть статус
# max_uses_reached), то есть остаётся рабочим и после записи в лог. Поэтому в
# логи идёт маска, а цепочка собирается по telegram_id и id записи.
from app.utils.security import mask_secret

from app.handlers.user.start.marketing_links import (
    _handle_promo_link_start,
    _handle_stats_link_click,
)
from app.handlers.user.start.share_discount import _handle_share_discount_start
from app.handlers.user.start.stage_gate import _show_stage_gate

router = Router()
logger = logging.getLogger(__name__)


def _start_payload(message: Message) -> Optional[str]:
    """Хвост команды /start, если он похож на нашу ссылку. Иначе None.

    ПОЧЕМУ ОТДЕЛЬНОЙ ФУНКЦИЕЙ

        Ниже семь веток диплинков (ref_, refd_, gift_, bgift_, s-, p- и
        токен привязки сайта), и каждая раньше сама резала message.text
        одним и тем же `strip().split(maxsplit=1)`. Семь копий разбора
        одной строки — семь мест, где разойдётся правило.

    ПОЧЕМУ ЭТО НЕ ПРОСТО УБОРКА

        Проверка «подозрительный payload» стояла в начале обработчика,
        писала предупреждение в лог и заканчивалась `pass`. Комментарий
        обещал «обрабатываем как обычный /start», но payload после этого
        разбирался ветками заново — то есть проверка не делала ничего.
        Теперь отбракованный payload виден как None, и ветки его не
        трогают: поведение стало тем, которое было написано в
        комментарии.

        Допустимый набор — буквы, цифры, `_` и `-`, не длиннее 64. Ровно
        столько разрешает и сам Telegram в deep-link, так что настоящая
        ссылка под ограничение попадает всегда; не попадает только то,
        что человек дописал руками.
    """
    if not message.text:
        return None
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None

    payload = parts[1]
    if len(payload) > 64 or not payload.replace("_", "").replace("-", "").isalnum():
        logger.warning(
            "INVALID_START_PAYLOAD user=%s payload=%s",
            message.from_user.id,
            payload[:30],
        )
        return None
    return payload


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    # SECURITY: Только private chat
    if message.chat.type != "private":
        return

    # Единственный разбор хвоста /start на весь обработчик. None означает
    # «обычный /start» — и для команды без ссылки, и для отбракованной.
    start_payload = _start_payload(message)

    await state.clear()
    # SAFE STARTUP GUARD: Проверка готовности БД
    # /start может работать в деградированном режиме (только показ меню),
    # но если БД недоступна, не пытаемся создавать пользователя
    if not database.DB_READY:
        # В STAGE показываем меню без сообщения об ошибке (read-only режим)
        # В PROD показываем сообщение об ошибке
        language = await resolve_user_language(message.from_user.id)
        text = i18n_get_text(language, "main.welcome")
        if config.IS_PROD:
            text += "\n\n" + i18n_get_text(language, "main.service_unavailable")
        keyboard = await get_main_menu_keyboard(language, message.from_user.id)
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        return
    # Обработчик команды /start
    telegram_id = message.from_user.id
    # Single DB fetch — extract language directly (avoid duplicate get_user call)
    user = await database.get_user(telegram_id)
    is_new_user = user is None
    start_language = (user.get("language") or "ru") if user else "ru"

    # STAGE GATE: новые пользователи в stage сначала выбирают «пользователь /
    # разработчик». Пользователь — редирект на prod-бот по реф-ссылке, разработчик —
    # продолжение во flow. В prod этот блок никогда не срабатывает.
    if config.IS_STAGE and is_new_user:
        await _show_stage_gate(message)
        return
    # Safe username resolution: username or first_name or localized fallback
    username = safe_resolve_username(message.from_user, start_language, telegram_id)
    # Ограничиваем длину для БД
    if username and len(username) > 64:
        username = username[:64]

    # Создаем пользователя если его нет (user already fetched above)
    if not user:
        await database.create_user(telegram_id, username, start_language)
    else:
        # Update username + ensure referral_code in a single connection
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            if username is not None:
                await conn.execute(
                    "UPDATE users SET username = $1 WHERE telegram_id = $2",
                    username, telegram_id
                )
            if not user.get("referral_code"):
                referral_code = database.generate_referral_code(telegram_id)
                await conn.execute(
                    "UPDATE users SET referral_code = $1 WHERE telegram_id = $2 AND referral_code IS NULL",
                    referral_code, telegram_id
                )
    
    # SITE LINK: Обработка привязки с сайта /start <telegramLinkToken>
    # Сайт генерирует ссылку t.me/atlassecure_bot?start=<token>
    # Бот вызывает POST /api/bot/link чтобы привязать telegram_id к аккаунту сайта
    if start_payload:
        payload = start_payload
        # Токен привязки — не ref_, не gift_ и не bgift_ (длина 10-64;
        # набор символов уже проверен в _start_payload).
        if (not payload.startswith("ref_")
                and not payload.startswith("gift_")
                and not payload.startswith("bgift_")
                and len(payload) >= 10):
                try:
                    from app.services.site_sync import (
                        link_telegram_account, sync_balance, sync_referrals,
                        is_enabled as _site_enabled,
                    )
                    if _site_enabled():
                        link_result = await link_telegram_account(payload, telegram_id)
                        if link_result:
                            # payload[:16] при len(payload) >= 10 печатал токен
                            # ЦЕЛИКОМ для всех токенов короче 17 символов.
                            # Токен предъявительский: кто его прочитал, тот
                            # привязал свой Telegram к чужому аккаунту сайта,
                            # а следом идут sync_balance и sync_referrals —
                            # то есть чужие деньги. Маска обязана считать длину
                            # сама; любой срез по фиксированному числу символов
                            # снова откроет короткие токены целиком.
                            logger.info(
                                "SITE_LINK_SUCCESS user=%s token=%s",
                                telegram_id, mask_secret(payload),
                            )
                            # Mark user as site-linked in local DB
                            # Колонка site_linked заводится при инициализации
                            # схемы (database/legacy_schema.py). Здесь её
                            # ALTER TABLE больше нет: он брал ACCESS
                            # EXCLUSIVE на users прямо в обработчике /start.
                            pool = await database.get_pool()
                            async with pool.acquire() as conn:
                                await conn.execute(
                                    "UPDATE users SET site_linked = TRUE WHERE telegram_id = $1",
                                    telegram_id,
                                )
                            # Sync data immediately after linking
                            sub = await database.get_subscription(telegram_id)
                            if sub and sub.get("expires_at"):
                                from app.services.site_sync import sync_subscription
                                exp_iso = sub["expires_at"].isoformat()
                                plan = (sub.get("subscription_type") or "basic").strip().lower()
                                await sync_subscription(telegram_id, exp_iso, plan)
                            await sync_balance(telegram_id)
                            await sync_referrals(telegram_id)
                            logger.info("SITE_LINK_FULL_SYNC user=%s", telegram_id)

                            await message.answer(
                                "✅ Сайт QoDev успешно привязан.\nТеперь синхронизация работает! ⚡️",
                                parse_mode="HTML",
                            )
                        else:
                            # Отказ привязки — тот же токен и тот же риск:
                            # link_telegram_account вернула ложь, но токен
                            # остался рабочим и лежит в логе.
                            logger.warning(
                                "SITE_LINK_FAILED user=%s token=%s",
                                telegram_id, mask_secret(payload),
                            )
                except Exception as e:
                    logger.warning("SITE_LINK_ERROR user=%s error=%s", telegram_id, e)

    # BYPASS GIFT LINK: /start bgift_<CODE> — admin-created GB gift link.
    # Grants the configured bypass GB through Remnawave; one redemption per user.
    if start_payload and start_payload.startswith("bgift_"):
        bgift_code = start_payload[6:]  # Strip "bgift_" prefix
        if bgift_code and 4 <= len(bgift_code) <= 32 and bgift_code.isalnum():
            language = await resolve_user_language(telegram_id)
            try:
                result = await database.redeem_bypass_gift_link(
                    code=bgift_code,
                    telegram_id=telegram_id,
                )
                status = result.get("status")
                # Default keyboard for non-success outcomes (errors).
                keyboard = (
                    get_language_keyboard(language) if is_new_user
                    else await get_main_menu_keyboard(language, telegram_id)
                )

                if status == "success":
                    gb = result.get("gb_amount") or 0
                    link_id = (result.get("link") or {}).get("id")
                    # Grant GB via Remnawave (creates account if user has none).
                    # We need to make sure there's an active subscription row so
                    # set_remnawave_uuid (WHERE status='active') can persist the
                    # UUID. But ensure_bypass_only_subscription clobbers an
                    # existing active row's expires_at to +10y — so only call it
                    # when the user has NO active subscription.
                    from app.services.remnawave_service import add_bypass_traffic
                    extra_bytes = int(gb) * 1024 * 1024 * 1024
                    granted = False
                    try:
                        existing_active = await database.get_subscription(telegram_id)
                        if not existing_active:
                            await database.ensure_bypass_only_subscription(telegram_id)
                        granted = await add_bypass_traffic(
                            telegram_id=telegram_id,
                            extra_bytes=extra_bytes,
                            subscription_type="basic",
                            subscription_end=None,
                            period_days=30,
                        )
                    except Exception as rmn_err:
                        logger.exception(
                            "BGIFT_REMNAWAVE_FAIL user=%s code=%s err=%s",
                            telegram_id, mask_secret(bgift_code), rmn_err,
                        )

                    if granted:
                        text = i18n_get_text(
                            language, "bypass_gift.activated",
                            gb=gb,
                        )
                        # Success keyboard: dedicated "Connect Bypass" button
                        # leading to the gift-only setup flow.
                        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
                        keyboard = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(
                                text=i18n_get_text(language, "bypass_gift.connect_btn"),
                                callback_data="bgift_setup",
                            )],
                        ])
                        logger.info(
                            "BGIFT_REDEEMED user=%s code=%s gb=%s",
                            telegram_id, mask_secret(bgift_code), gb,
                        )
                    else:
                        # Remnawave failed — roll back the redemption record so
                        # the user can retry without hitting the per-user
                        # uniqueness guard. Logs flag the issue for admin.
                        if link_id is not None:
                            try:
                                rolled_back = await database.rollback_bypass_gift_redemption(
                                    link_id, telegram_id,
                                )
                                logger.error(
                                    "BGIFT_REMNAWAVE_FAIL_ROLLBACK user=%s code=%s gb=%s rolled_back=%s",
                                    telegram_id, mask_secret(bgift_code), gb, rolled_back,
                                )
                            except Exception as rb_err:
                                logger.exception(
                                    "BGIFT_ROLLBACK_FAIL user=%s code=%s err=%s",
                                    telegram_id, mask_secret(bgift_code), rb_err,
                                )
                        text = i18n_get_text(language, "bypass_gift.error_remnawave")
                    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
                    return

                error_keys = {
                    "already_redeemed": "bypass_gift.error_already_redeemed",
                    "expired": "bypass_gift.error_expired",
                    "max_uses_reached": "bypass_gift.error_max_uses",
                    "deleted": "bypass_gift.error_not_found",
                    "not_found": "bypass_gift.error_not_found",
                }
                text = i18n_get_text(
                    language, error_keys.get(status, "bypass_gift.error_not_found"),
                )
                await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
                logger.info(
                    "BGIFT_REDEMPTION_FAILED user=%s code=%s status=%s",
                    telegram_id, mask_secret(bgift_code), status,
                )
                return
            except Exception as e:
                logger.exception(
                    "BGIFT_REDEMPTION_ERROR user=%s code=%s err=%s",
                    telegram_id, mask_secret(bgift_code), e,
                )
                text = i18n_get_text(language, "bypass_gift.error_not_found")
                keyboard = (
                    get_language_keyboard(language) if is_new_user
                    else await get_main_menu_keyboard(language, telegram_id)
                )
                await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
                return

    # GIFT ACTIVATION: Обработка подарочной ссылки /start gift_XXXXX
    if start_payload and start_payload.startswith("gift_"):
        gift_code = start_payload[5:]  # Убираем "gift_" префикс
        if gift_code and len(gift_code) <= 20 and gift_code.isalnum():
            try:
                activation_result = await database.activate_gift_subscription(
                    gift_code=gift_code,
                    activated_by=telegram_id,
                )
                language = await resolve_user_language(telegram_id)

                if activation_result["success"]:
                    tariff = activation_result["tariff"]
                    period_days = activation_result["period_days"]
                    tariff_name = "Basic" if tariff == "basic" else "Plus"
                    months = period_days // 30
                    if months == 1:
                        period_text = "1 месяц"
                    elif months in (2, 3, 4):
                        period_text = f"{months} месяца"
                    else:
                        period_text = f"{months} месяцев"

                    if is_new_user:
                        # Новый пользователь: приветствие + активация + выбор языка.
                        #
                        # Текст проходит через реестр автоуведомлений: у
                        # админа в дашборде есть тумблер и поле для этого
                        # ключа, и раньше они не делали ничего — сообщение
                        # брали напрямую из i18n. Для нерусских языков
                        # реестр возвращает None (там хранится только
                        # русский), и тогда берём перевод, как и раньше.
                        from app.services.automated_notifications import (
                            get_notification_text as _autonotif_text,
                        )
                        _params = {"tariff_name": tariff_name, "period": period_text}
                        text = await _autonotif_text(
                            "gift.activated_welcome", language=language, params=_params,
                        ) or i18n_get_text(
                            language, "gift.activated_welcome", **_params,
                        )
                        await message.answer(
                            text,
                            reply_markup=get_language_keyboard(language),
                            parse_mode="HTML",
                        )
                    else:
                        # Существующий пользователь: активация + главное меню
                        text = i18n_get_text(
                            language, "gift.activated",
                            tariff_name=tariff_name,
                            period=period_text,
                        )
                        keyboard = await get_main_menu_keyboard(language, telegram_id)
                        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
                    logger.info(f"GIFT_ACTIVATED_VIA_LINK user={telegram_id} code={mask_secret(gift_code)} new_user={is_new_user}")

                    # Fire-and-forget: create Remnawave bypass for gift recipient
                    try:
                        from app.services.remnawave_service import renew_remnawave_user_bg
                        if tariff in ("basic", "plus"):
                            sub = await database.get_subscription(telegram_id)
                            if sub and sub.get("expires_at"):
                                renew_remnawave_user_bg(telegram_id, tariff, sub["expires_at"])
                    except Exception as rmn_err:
                        logger.warning("REMNAWAVE_GIFT_FAIL: tg=%s %s", telegram_id, rmn_err)

                    return
                else:
                    error = activation_result.get("error", "unknown")
                    error_keys = {
                        "not_found": "gift.error_not_found",
                        "already_activated": "gift.error_already_activated",
                        "expired": "gift.error_expired",
                        "self_activation": "gift.error_self_activation",
                        "invalid_status": "gift.error_invalid",
                    }
                    error_key = error_keys.get(error, "gift.error_invalid")
                    text = i18n_get_text(language, error_key)
                    if is_new_user:
                        keyboard = get_language_keyboard(language)
                    else:
                        keyboard = await get_main_menu_keyboard(language, telegram_id)
                    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
                    logger.warning(f"GIFT_ACTIVATION_FAILED user={telegram_id} code={mask_secret(gift_code)} error={error}")
                    return
            except Exception as e:
                logger.exception(f"Gift activation error: user={telegram_id}, code={mask_secret(gift_code)}, error={e}")
                language = await resolve_user_language(telegram_id)
                text = i18n_get_text(language, "gift.error_invalid")
                if is_new_user:
                    keyboard = get_language_keyboard(language)
                else:
                    keyboard = await get_main_menu_keyboard(language, telegram_id)
                await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
                return

    # STATS LINK: /start s-<slug> — attribution + click log.
    # НЕ прерывает основной flow — просто пишет клик и (для новых юзеров)
    # проставляет acquired_via_stat_link_id. Дальше юзер идёт по обычному
    # пути (выбор языка / главное меню). Prefix `s-` короткий,
    # непохожий на refd_/ref_.
    if start_payload and start_payload.startswith("s-"):
        _slug = start_payload[2:]
        try:
            await _handle_stats_link_click(telegram_id, _slug, is_new_user)
        except Exception as e:
            logger.warning("STATS_LINK_CLICK_FAIL user=%s slug=%s err=%s",
                           telegram_id, _slug[:12], e)

    # PROMO LINK: /start p-<slug> — выдача награды (подписка / скидка /
    # ГБ). Рендерит финальный экран сам и возвращает True; если что-то
    # пошло не так (лимиты, expired) — тоже рендерит понятную ошибку.
    if start_payload and start_payload.startswith("p-"):
        _slug = start_payload[2:]
        handled = await _handle_promo_link_start(
            message, state, telegram_id, _slug, is_new_user,
        )
        if handled:
            return

    # SHARE-DISCOUNT LINK: /start refd_<code> — recipient gets 30%/24h
    # discount on basic/plus/combo. Lifetime-once per telegram_id (claim
    # tracked in `referral_share_discount_claims`). For new users we ALSO
    # set up the referral relationship (immutable), per product spec.
    # Handled BEFORE the regular `ref_` branch — `refd_` doesn't match
    # `ref_` via startswith, but order is also clearer this way.
    if start_payload and start_payload.startswith("refd_"):
        refd_code = start_payload[5:]  # strip "refd_"
        handled = await _handle_share_discount_start(
            message, state, telegram_id, refd_code, is_new_user,
        )
        if handled:
            return  # Already rendered final screen — done.

    # 1. REFERRAL REGISTRATION: Process ONLY for new users
    # Protects against: self-referral and existing users clicking referral links later
    referral_result = None
    if is_new_user:
        referral_result = await process_referral_on_first_interaction(message, telegram_id)
    else:
        # Existing user clicked a referral link — ignore and log
        if start_payload and start_payload.startswith("ref_"):
            logger.warning(
                "REFERRAL_BLOCKED_EXISTING_USER user=%s payload=%s",
                telegram_id, start_payload[:30]
            )
    
    # Send notification to referrer if just registered
    if referral_result and referral_result.get("should_notify"):
        try:
            referrer_id = referral_result.get("referrer_id")
            if referrer_id:
                # Текущий тир-процент реферрера для подстановки в пуш.
                ref_stats = await database.get_referral_statistics(referrer_id)
                ref_percent = int(ref_stats.get("cashback_percent", 10))
                from app.services.notifications.loyalty_pushes import pick_signup_push
                notification_text = pick_signup_push(ref_percent)

                await message.bot.send_message(
                    chat_id=referrer_id,
                    text=notification_text,
                    parse_mode="HTML",
                )
                
                logger.info(
                    f"REFERRAL_NOTIFICATION_SENT [type=registration, referrer={referrer_id}, "
                    f"referred={telegram_id}]"
                )
        except Exception as e:
            # Non-critical - log but don't fail
            logger.warning(
                "NOTIFICATION_FAILED",
                extra={
                    "type": "referral_registration",
                    "referrer": referral_result.get("referrer_id"),
                    "referred": telegram_id,
                    "error": str(e)
                }
            )
    
    # Phase 4: ALWAYS show language selection first (pre-language-binding screen)
    text = i18n_get_text(start_language, "lang.select_title")
    await message.answer(text, reply_markup=get_language_keyboard(start_language), parse_mode="HTML")
