"""Совместимостные заглушки на месте снятого с эксплуатации xray-API.

Единственный VPN-бэкенд — Remnawave (app.services.purchase_flow,
app.services.remnawave_premium). Здесь остались только точки входа,
которые ещё зовут остаточные пути (восстановление в auto_renewal,
админский перевыпуск, очистка триалов), плюс генерация ссылки подписки.

Ходить по HTTP отсюда больше некуда, поэтому вместе с xray-веткой уехали
её обвязка и типы ошибок — держать их значит утверждать в коде поведение,
которого нет. Что именно удалено и почему — в комментариях ниже по файлу.
"""
import httpx
import logging
import asyncio
from datetime import datetime
from typing import Dict, Optional
import config

logger = logging.getLogger(__name__)

# Здесь жила обвязка HTTP-вызовов к xray: _fire_and_forget с реестром
# фоновых задач _background_tasks (аудит-логи отправлялись мимо основного
# потока), VPN_HTTP_TIMEOUT/HTTP_TIMEOUT/MAX_RETRIES/RETRY_DELAY и импорт
# app.utils.retry.retry_async. Ни одного вызова из репозитория не
# осталось: сетевых запросов в модуле нет. Своя обвязка фоновых задач
# есть у Remnawave — app/services/remnawave_service.py:_fire_and_forget.


class VPNAPIError(Exception):
    """Базовый класс для ошибок VPN API.

    Оставлен: его бросает safe_remove_vless_user_with_retry, и её
    вызывающие ловят именно этот тип при откате провижининга.
    """
    pass


# Здесь были VPNTimeoutError, AuthError, InvalidResponseError и
# CriticalUUIDMismatchError. Все четыре описывали ответы xray-API, и
# бросать их стало некому. Опаснее всего был VPNTimeoutError: его ещё
# ловил снятый слой app/services/vpn — читалось это как «таймауты
# обрабатываются», хотя обработчик был недостижим.
#
# Тут же были _validate_uuid_no_prefix (запрет префиксов stage-/prod- в
# UUID) и _validate_api_url_security (HTTPS и запрет приватных IP для
# XRAY_API_URL). Обе проверяли аргументы удалённых HTTP-функций;
# XRAY_API_URL из оборота выведен, проверять нечего.


# Здесь была check_xray_health — GET /health на снятый с эксплуатации
# сервер. Её никто не вызывал: живость панели проверяет healthcheck.py
# через Remnawave.


async def add_vless_user(
    telegram_id: int,
    subscription_end: datetime,
    uuid: Optional[str] = None,
    tariff: str = "basic",
) -> Dict[str, str]:
    """Совместимостная заглушка вместо снятого с эксплуатации samopis xray.

    Провижининг выполняется через app.services.purchase_flow (Remnawave).
    Функция сохранена, потому что её ещё вызывают остаточные пути —
    восстановление в auto_renewal и админский перевыпуск: им нужен словарь
    ожидаемой формы, а не исключение.

    Возвращает переданный uuid (или новый) и пустые ссылки: настоящие
    ссылки выдаёт Remnawave.

    ИНВАРИАНТ: не вызывать внутри активной транзакции БД (риск сироты-UUID).
    """
    logger.info(
        "VPN_UTILS_ADD_NOOP: tg=%s — samopis снят с эксплуатации, "
        "провижининг идёт через app.services.purchase_flow",
        telegram_id,
    )
    from uuid import uuid4
    return {
        "uuid": uuid or str(uuid4()),
        "vless_url": "",
        "vless_url_plus": None,
        "subscription_type": tariff or "basic",
    }


# Здесь была ensure_user_in_xray — «синхронизировать пользователя с
# xray»: попробовать update, при 404 добавить с тем же UUID. Обе её
# ветки вели в заглушки выше и ниже по файлу, то есть вся функция
# сводилась к записи в лог.
#
# Единственным её вызовом был блок в auto_renewal перед настоящим
# продлением через Remnawave — читать его приходилось как рабочий код.
# Продление подписки в панели делает app.services.purchase_flow.
# sync_renewal_to_remnawave, его и зовут остальные места.


async def update_vless_user(uuid: str, subscription_end: datetime) -> None:
    """Совместимостная заглушка: срок премиума продлевает Remnawave.

    Обновление expireAt выполняется в
    app.services.remnawave_premium.renew_premium_user на пути продления.
    Функция оставлена, чтобы остаточные вызовы (восстановление в
    auto_renewal, админский перевыпуск) не падали.
    """
    logger.info(
        "VPN_UTILS_UPDATE_NOOP: uuid=%s — samopis снят с эксплуатации",
        (uuid or "")[:8] + "...",
    )


async def remove_vless_user(uuid: str) -> None:
    """Совместимостная заглушка: удаление выполняет Remnawave.

    Реальная очистка идёт через панель
    (app.services.remnawave_premium.disable_premium_user и соседние).
    Функция оставлена, чтобы остаточные вызовы не падали: очистка триалов,
    админский отзыв доступа и откат транзакции при защите от сирот-UUID.
    """
    logger.info(
        "VPN_UTILS_REMOVE_NOOP: uuid=%s — samopis снят с эксплуатации",
        (uuid or "")[:8] + "...",
    )


async def safe_remove_vless_user_with_retry(uuid: str, *, max_retries: int = 3) -> None:
    """Компенсация отката, оставшаяся без исполнителя: удалять здесь нечем.

    Внутри цикла зовётся remove_vless_user — заглушка выше по файлу: xray
    снят с эксплуатации, HTTP-вызова нет, исключений нет, retry никогда не
    срабатывает. Функция сохранена, потому что её ещё зовут пути отката
    (activation, balance_purchases, admin_access, subscription_state).

    ВАЖНО ПРО ЗАПИСИ: успешный выход из этой функции НЕ означает, что
    сущность где-то удалена. Раньше здесь стояло ORPHAN_CLEANUP_SUCCESS, и
    вызывающие писали поверх «удалено» / «сирота предотвращена» — все эти
    записи были ложны всегда, потому что под ними не было действия.
    Настоящее удаление делает remnawave_api.delete_user (образец —
    database/purchase_finalization.py, компенсация фазы 2).
    """
    uuid_clean = str(uuid).strip() if uuid else ""
    if not uuid_clean:
        raise ValueError("safe_remove_vless_user_with_retry: uuid required")
    uuid_preview = uuid_clean[:8] if len(uuid_clean) > 8 else "***"
    last_error = None
    for attempt in range(max_retries):
        try:
            if attempt == 0:
                logger.info(
                    "ORPHAN_CLEANUP_ATTEMPT",
                    extra={"uuid": uuid_preview, "attempt": attempt + 1}
                )
            else:
                delay = 2 ** (attempt - 1)  # 1s, 2s, 4s exponential backoff
                logger.warning(
                    "ORPHAN_CLEANUP_RETRY",
                    extra={"uuid": uuid_preview, "attempt": attempt + 1, "retries": max_retries, "delay_s": delay}
                )
                await asyncio.sleep(delay)
            await remove_vless_user(uuid_clean)
            logger.info(
                "ORPHAN_CLEANUP_NOOP: uuid=%s — xray-заглушка, ничего не удалено; "
                "если сущность создавалась в панели, её чистит вызывающий через "
                "remnawave_api.delete_user либо админ вручную по этому uuid",
                uuid_preview,
            )
            return
        except (httpx.HTTPError, httpx.TimeoutException, ConnectionError, OSError) as e:
            last_error = e
            if attempt == max_retries - 1:
                break
            continue
        except Exception as e:
            last_error = e
            break
    logger.critical(
        "ORPHAN_CLEANUP_FAILED",
        extra={"uuid": uuid_preview, "retries": max_retries, "error": str(last_error)[:200] if last_error else "unknown"}
    )
    raise VPNAPIError(f"Orphan cleanup failed after {max_retries} retries: {last_error}") from last_error


# Здесь была reissue_vpn_access — «удалить старый UUID и создать
# новый». Её никто не вызывал, и вызвать было нельзя: внутри она
# обращается к заглушке add_vless_user, та возвращает пустой
# vless_url, и функция сама же бросала на него VPNAPIError.
#
# Перевыпуск ключа делает database.reissue_subscription_key через
# reissue_premium_user_entity в Remnawave.


# ============================================================================
# Subscription URL generation (matches mini-app's /api/sub/{token}?id={id})
# ============================================================================

def generate_sub_token(bot_token: str, telegram_id: int) -> str:
    """
    HMAC-SHA256(bot_token, str(telegram_id)) → base64url → first 32 chars.
    Identical to the Node.js implementation in the mini-app.
    """
    import hmac
    import hashlib
    import base64

    signature = hmac.new(
        bot_token.encode(),
        str(telegram_id).encode(),
        hashlib.sha256,
    ).digest()
    token = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()[:32]
    return token


def build_sub_url(telegram_id: int) -> str:
    """
    Build the subscription URL for a user:
    https://{APP_URL}/api/sub/{token}?id={telegram_id}
    """
    token = generate_sub_token(config.BOT_TOKEN, telegram_id)
    return f"{config.SUB_BASE_URL}/api/sub/{token}?id={telegram_id}"
