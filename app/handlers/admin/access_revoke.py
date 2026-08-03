"""Отзыв доступа, VIP, перевыпуск ключа и удаление пользователя.

ЧТО ОБЪЕДИНЯЕТ ЭТИ ЭКРАНЫ
    Все они отнимают или пересоздают то, что у пользователя уже есть,
    поэтому каждый требует подтверждения и заканчивается уведомлением.

ЧТО ЛЕГКО СЛОМАТЬ
    Перевыпуск ключа идёт под блокировкой (get_reissue_lock): два
    параллельных нажатия создали бы две сущности в панели, и одна осталась
    бы сиротой. Удаление пользователя намеренно НЕ трогает финансовые
    таблицы — см. database.admin.admin_delete_user_complete.
"""
import logging
import asyncio
import config
import database
import uuid
import vpn_utils
from aiogram import Router, F
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from app.handlers.common.keyboards import get_reissue_notification_keyboard
from app.handlers.common.states import AdminGrantAccess, AdminGrantState, AdminRevokeAccess, AdminUserSearch
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from datetime import datetime, timedelta, timezone
from app.handlers.admin.keyboards import (
    get_admin_back_keyboard,
    get_admin_user_keyboard,
    get_admin_user_keyboard_processing,
)
from app.handlers.common.utils import safe_edit_text, get_reissue_lock, get_reissue_notification_text
from app.handlers.admin._user_card import _show_admin_user_card

admin_revoke_router = Router()
logger = logging.getLogger(__name__)


@admin_revoke_router.callback_query(F.data.startswith("admin:revoke:user:"))
async def callback_admin_revoke(callback: CallbackQuery, bot: Bot, state: FSMContext):
    """
    1️⃣ CALLBACK DATA SCHEMA (точечно)
    2️⃣ FIX handler callback_admin_revoke
    
    Admin revoke access - ask for notify choice first.
    Handler обрабатывает ТОЛЬКО callback вида: admin:revoke:user:<id>
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        # 2️⃣ FIX: Строгий guard - парсим только admin:revoke:user:<id>
        parts = callback.data.split(":")
        if len(parts) != 4 or parts[2] != "user":
            logger.warning(f"Invalid revoke callback format: {callback.data}")
            await callback.answer("Ошибка формата команды", show_alert=True)
            return
        
        user_id = int(parts[3])
        
        # 4️⃣ FSM CONSISTENCY: Save user_id and ask for notify choice
        await state.update_data(user_id=user_id)
        
        text = i18n_get_text(language, "admin.revoke_confirm_text", "admin_revoke_confirm_text")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_yes"), callback_data="admin:revoke:notify:yes")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.notify_no"), callback_data="admin:revoke:notify:no")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.cancel", "admin_cancel"), callback_data=f"admin:user")],
        ])
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await state.set_state(AdminRevokeAccess.waiting_for_notify_choice)
        
        # 5️⃣ ЛОГИРОВАНИЕ: выбран user_id
        logger.info(f"Admin {callback.from_user.id} initiated revoke for user {user_id}")
        logger.debug(f"FSM: AdminRevokeAccess.waiting_for_notify_choice set for user {user_id}")
        
    except ValueError as e:
        logger.error(f"Invalid user_id in revoke callback: {callback.data}, error: {e}")
        await callback.answer("Ошибка: неверный ID пользователя", show_alert=True)
        await state.clear()
    except Exception as e:
        logger.exception(f"Error in callback_admin_revoke: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@admin_revoke_router.callback_query(F.data.startswith("admin:revoke:notify:"), StateFilter(AdminRevokeAccess.waiting_for_notify_choice))
async def callback_admin_revoke_notify(callback: CallbackQuery, bot: Bot, state: FSMContext):
    """
    3️⃣ ДОБАВИТЬ ОТДЕЛЬНЫЙ handler для notify
    
    Execute revoke with notify_user choice.
    Handler обрабатывает ТОЛЬКО callback вида: admin:revoke:notify:yes|no
    """
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    await callback.answer()
    
    try:
        # 1️⃣ НОРМАЛИЗАЦИЯ notify (КРИТИЧНО): читаем notify=yes|no
        parts = callback.data.split(":")
        if len(parts) != 4 or parts[2] != "notify":
            logger.warning(f"Invalid revoke notify callback format: {callback.data}")
            await callback.answer("Ошибка формата команды", show_alert=True)
            await state.clear()
            return
        
        # 1️⃣ НОРМАЛИЗАЦИЯ notify: явно приводим к bool
        notify_raw = parts[3]  # "yes" or "no"
        notify = notify_raw == "yes"  # bool: True or False
        
        # 4️⃣ FSM CONSISTENCY: используем сохраненный user_id
        data = await state.get_data()
        user_id = data.get("user_id")
        
        if not user_id:
            logger.error(f"user_id not found in FSM state for revoke notify")
            await callback.answer("Ошибка: user_id не найден", show_alert=True)
            await state.clear()
            return
        
        # 1️⃣ НОРМАЛИЗАЦИЯ notify: сохраняем в FSM ТОЛЬКО bool
        await state.update_data(notify=notify)
        
        # 4️⃣ ЛОГИРОВАНИЕ: при выборе notify
        logger.info(f"ADMIN_REVOKE_NOTIFY_SELECTED [user_id={user_id}, notify={notify}]")
        
        # 3️⃣ ДОБАВИТЬ ОТДЕЛЬНЫЙ handler: вызываем финальный revoke action
        revoked = await database.admin_revoke_access_atomic(
            telegram_id=user_id,
            admin_telegram_id=callback.from_user.id
        )

        # Fire-and-forget: disable Remnawave bypass
        if revoked:
            try:
                from app.services.remnawave_service import disable_remnawave_user_bg
                disable_remnawave_user_bg(user_id)
            except Exception as rmn_err:
                logger.warning("REMNAWAVE_ADMIN_REVOKE_FAIL: tg=%s %s", user_id, rmn_err)

        if not revoked:
            text = "❌ У пользователя нет активной подписки"
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            await callback.answer("Нет активной подписки", show_alert=True)
        else:
            text = "✅ Доступ отозван"
            if notify:
                text += "\nПользователь уведомлён."
            else:
                text += "\nДействие выполнено без уведомления."
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            
            # 2️⃣ ПРОВЕРКА notify В ФИНАЛЬНОМ revoke: используем ТОЛЬКО if notify:
            # 3️⃣ ОТПРАВКА УВЕДОМЛЕНИЯ (ЯВНО): если notify=True
            if notify:
                # 5️⃣ ЗАЩИТА ОТ ТИХОГО ПРОПУСКА: проверяем telegram_id
                if not user_id:
                    logger.warning(f"ADMIN_REVOKE_NOTIFY_SKIP: user_id missing, notify=True but cannot send")
                else:
                    try:
                        # 3️⃣ ОТПРАВКА УВЕДОМЛЕНИЯ: используем telegram_id из FSM (НЕ из callback)
                        # 3️⃣ ОТПРАВКА УВЕДОМЛЕНИЯ: текст без форматных рисков (фиксированный)
                        # Use unified notification service
                        import admin_notifications
                        user_text = (
                            "Ваш доступ был отозван администратором.\n"
                            "Если вы считаете это ошибкой — обратитесь в поддержку."
                        )
                        success = await admin_notifications.send_user_notification(
                            bot=bot,
                            user_id=user_id,
                            message=user_text,
                            notification_type="admin_revoke"
                        )
                        if success:
                            # 4️⃣ ЛОГИРОВАНИЕ: при отправке уведомления
                            logger.info(f"NOTIFICATION_SENT [type=admin_revoke, user_id={user_id}]")
                    except Exception as e:
                        logger.exception(f"Error sending notification to user {user_id}: {e}")
                        # Не прерываем выполнение - revoke уже выполнен
            else:
                # 4️⃣ ЛОГИРОВАНИЕ: если notify=False
                logger.info(f"ADMIN_REVOKE_NOTIFY_SKIPPED [user_id={user_id}]")
            
            # Audit log
            await database._log_audit_event_atomic_standalone(
                "admin_revoke_access",
                callback.from_user.id,
                user_id,
                f"Admin revoked access, notify_user={notify}"
            )
        
        # 3️⃣ ДОБАВИТЬ ОТДЕЛЬНЫЙ handler: корректно завершаем FSM
        await state.clear()
        logger.debug(f"FSM: AdminRevokeAccess cleared after revoke")
        
    except Exception as e:
        logger.exception(f"Error in callback_admin_revoke_notify: {e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        await state.clear()


@admin_revoke_router.callback_query(F.data.startswith("admin:vip_grant:"))
async def callback_admin_vip_grant(callback: CallbackQuery):
    """Обработчик кнопки 'Выдать VIP'"""
    # B3.3 - ADMIN OVERRIDE: Admin operations intentionally bypass system_state checks
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        user_id = int(callback.data.split(":")[2])
        
        # Проверяем, есть ли уже VIP-статус
        existing_vip = await database.is_vip_user(user_id)
        if existing_vip:
            # Если уже есть VIP, просто обновляем карточку
            await _show_admin_user_card(callback.message, user_id, callback.from_user.id)
            await callback.answer("VIP уже назначен", show_alert=True)
            return
        
        # Назначаем VIP-статус
        success = await database.grant_vip_status(
            telegram_id=user_id,
            granted_by=callback.from_user.id
        )
        
        if success:
            # После успешного назначения VIP обновляем карточку пользователя
            await _show_admin_user_card(callback.message, user_id, callback.from_user.id)
            await callback.answer("✅ VIP-статус выдан", show_alert=True)
        else:
            text = "❌ Ошибка при назначении VIP-статуса"
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_vip_grant: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@admin_revoke_router.callback_query(F.data.startswith("admin:vip_revoke:"))
async def callback_admin_vip_revoke(callback: CallbackQuery):
    """Обработчик кнопки 'Снять VIP'"""
    # B3.3 - ADMIN OVERRIDE: Admin operations intentionally bypass system_state checks
    user = await database.get_user(callback.from_user.id)
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return
    
    try:
        user_id = int(callback.data.split(":")[2])
        
        # Отзываем VIP-статус
        success = await database.revoke_vip_status(
            telegram_id=user_id,
            revoked_by=callback.from_user.id
        )
        
        if success:
            # После успешного снятия VIP обновляем карточку пользователя
            await _show_admin_user_card(callback.message, user_id, callback.from_user.id)
            await callback.answer("✅ VIP-статус снят", show_alert=True)
        else:
            text = "❌ VIP-статус не найден или уже снят"
            await safe_edit_text(callback.message, text, reply_markup=get_admin_back_keyboard(language))
            await callback.answer("VIP не найден", show_alert=True)
        
    except Exception as e:
        logging.exception(f"Error in callback_admin_vip_revoke: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@admin_revoke_router.callback_query(F.data.startswith("admin:user_reissue:"))
async def callback_admin_user_reissue(callback: CallbackQuery):
    """Перевыпуск ключа из админ-дашборда. 5 слоёв защиты: immediate ACK, disabled UI, in-memory lock, Postgres advisory lock, correlation logging."""
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return

    try:
        target_user_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Ошибка: неверный формат команды", show_alert=True)
        return

    # STEP 3 — IN-MEMORY ASYNC LOCK (fast UX check + real acquire)
    lock = get_reissue_lock(target_user_id)
    logger.debug("ADMIN_REISSUE_LOCK_ATTEMPT user=%s locked=%s", target_user_id, lock.locked())
    
    # STEP 1 — FAST CHECK (UX guard only)
    if lock.locked():
        logger.info("ADMIN_REISSUE_REJECTED_ALREADY_RUNNING user=%s", target_user_id)
        await callback.answer("Перевыпуск уже выполняется...", show_alert=False)
        return

    # STEP 2 — ACQUIRE (real acquire, no timeout)
    await lock.acquire()

    try:
        # STEP 1 — IMMEDIATE CALLBACK ACK (inside protected block to prevent lock leak)
        await callback.answer("Перевыпуск ключа запущен...", show_alert=False)
        correlation_id = str(uuid.uuid4())
        update_id = getattr(getattr(callback, "update", None), "update_id", None)
        logger.info(
            "ADMIN_REISSUE_START",
            extra={
                "correlation_id": correlation_id,
                "admin_id": callback.from_user.id,
                "target_user_id": target_user_id,
                "callback_id": callback.id,
                "update_id": update_id,
                "task_id": id(asyncio.current_task()),
            },
        )

        # STEP 2 — DISABLE BUTTON DURING PROCESSING
        try:
            await callback.message.edit_reply_markup(
                reply_markup=get_admin_user_keyboard_processing(target_user_id, language=language)
            )
        except TelegramBadRequest:
            pass  # Message may be edited by other handler

        admin_telegram_id = callback.from_user.id
        result = await database.reissue_vpn_key_atomic(
            target_user_id, admin_telegram_id, correlation_id=correlation_id
        )
        new_vpn_key, old_vpn_key = result

        if new_vpn_key is None:
            await safe_edit_text(
                callback.message,
                "❌ Не удалось перевыпустить ключ. Нет активной подписки или ошибка создания ключа.",
                reply_markup=get_admin_back_keyboard(language),
            )
            return

        # STEP 6 — RESTORE KEYBOARD AFTER SUCCESS
        user = await database.get_user(target_user_id)
        subscription = await database.get_subscription(target_user_id)
        is_vip = await database.is_vip_user(target_user_id)
        has_discount = await database.get_user_discount(target_user_id) is not None

        text = "👤 Информация о пользователе\n\n"
        text += f"Telegram ID: {target_user_id}\n"
        text += f"Username: @{user.get('username', 'не указан') if user else 'не указан'}\n\n"
        if subscription:
            expires_at = subscription["expires_at"]
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at)
            expires_str = expires_at.strftime("%d.%m.%Y %H:%M")
            text += "Статус подписки: ✅ Активна\n"
            text += f"Срок действия: до {expires_str}\n"
            text += f"VPN-ключ: <code>{new_vpn_key}</code>\n"
            text += f"\n✅ Ключ перевыпущен!\nСтарый ключ: {old_vpn_key[:20]}..."

        sub = await database.get_subscription(target_user_id)
        sub_type = (sub.get("subscription_type") or "basic").strip().lower() if sub else "basic"
        if sub_type not in config.VALID_SUBSCRIPTION_TYPES:
            sub_type = "basic"
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_user_keyboard(
                has_active_subscription=True,
                user_id=target_user_id,
                has_discount=has_discount,
                is_vip=is_vip,
                subscription_type=sub_type,
                language=language,
            ),
            parse_mode="HTML",
        )

        logger.info(
            "ADMIN_REISSUE_COMPLETE",
            extra={"correlation_id": correlation_id, "target_user_id": target_user_id},
        )

        # Уведомляем пользователя
        try:
            from vpn_utils import build_sub_url
            user_text = get_reissue_notification_text(build_sub_url(target_user_id))
            keyboard = get_reissue_notification_keyboard()
            await callback.bot.send_message(target_user_id, user_text, reply_markup=keyboard, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Error sending reissue notification to user {target_user_id}: {e}")

    except Exception as e:
        logging.exception(f"Error in callback_admin_user_reissue: {e}")
        try:
            await safe_edit_text(
                callback.message,
                "❌ Ошибка при перевыпуске ключа. Проверь логи.",
                reply_markup=get_admin_back_keyboard(language),
            )
        except Exception:
            pass
    finally:
        # GUARANTEED RELEASE (lock was acquired, no check needed)
        lock.release()


@admin_revoke_router.callback_query(F.data.startswith("admin:delete_user:"))
async def callback_admin_delete_user(callback: CallbackQuery):
    """Показываем подтверждение удаления пользователя из БД"""
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return

    await callback.answer()

    try:
        parts = callback.data.split(":")
        if len(parts) != 3:
            await callback.answer("Ошибка формата команды", show_alert=True)
            return

        user_id = int(parts[2])

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="✅ Подтвердить удаление",
                callback_data=f"admin:delete_user_confirm:{user_id}"
            )],
            [InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=f"admin:user_back:{user_id}"
            )],
        ])
        await callback.message.edit_text(
            f"⚠️ Вы точно хотите удалить пользователя <b>{user_id}</b> из базы данных?\n\n"
            "Будут удалены ВСЕ данные:\n"
            "• Профиль пользователя\n"
            "• Подписка и VPN-ключ\n"
            "• История платежей\n"
            "• Баланс\n"
            "• Реферальные данные\n"
            "• Скидки и VIP-статус\n\n"
            "❗️ Это действие необратимо!",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except ValueError:
        await callback.answer("Ошибка: неверный ID пользователя", show_alert=True)
    except Exception as e:
        logger.exception(f"Error in callback_admin_delete_user: {e}")
        await callback.answer("Произошла ошибка", show_alert=True)


@admin_revoke_router.callback_query(F.data.startswith("admin:delete_user_confirm:"))
async def callback_admin_delete_user_confirm(callback: CallbackQuery):
    """Подтверждение удаления — выполняем полное удаление пользователя из БД"""
    language = await resolve_user_language(callback.from_user.id)
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return

    await callback.answer()

    try:
        parts = callback.data.split(":")
        if len(parts) != 3:
            await callback.answer("Ошибка формата команды", show_alert=True)
            return

        user_id = int(parts[2])
        admin_id = callback.from_user.id

        success = await database.admin_delete_user_complete(user_id, admin_id)

        if success:
            logger.info(f"Admin {admin_id} deleted user {user_id} from DB completely")
            await callback.message.edit_text(
                f"✅ Пользователь <b>{user_id}</b> полностью удалён из базы данных.",
                reply_markup=get_admin_back_keyboard(language),
                parse_mode="HTML"
            )
        else:
            await callback.message.edit_text(
                f"❌ Пользователь <b>{user_id}</b> не найден в базе данных.",
                reply_markup=get_admin_back_keyboard(language),
                parse_mode="HTML"
            )
    except ValueError:
        await callback.answer("Ошибка: неверный ID пользователя", show_alert=True)
    except Exception as e:
        logger.exception(f"Error in callback_admin_delete_user_confirm: {e}")
        await callback.message.edit_text(
            "❌ Ошибка при удалении пользователя. Проверь логи.",
            reply_markup=get_admin_back_keyboard(language),
            parse_mode="HTML",
        )
