"""
Модуль для работы с Xray Core VPN API (VLESS + REALITY).

Этот модуль является единой точкой абстракции для работы с VPN инфраструктурой.
Все VPN операции должны выполняться через функции этого модуля.

STEP 1.3 - EXTERNAL DEPENDENCIES POLICY:
- VPN API unavailable → activation skipped, no errors raised
- VPN API disabled (VPN_ENABLED=False) → NOT treated as error, graceful degradation
- VPN API timeout → retried with exponential backoff (max 2 retries)
- VPN API 401/403 → AuthError raised immediately (NOT retried)
- VPN API 4xx → InvalidResponseError raised immediately (NOT retried)
- VPN API 5xx/timeout/network → retried with exponential backoff

STEP 3 — PART D: EXTERNAL DEPENDENCY ISOLATION
- All VPN API calls are isolated inside try/except blocks
- External failures are mapped to dependency_error
- External failure does NOT break handler/worker
- System continues degraded when VPN API unavailable
- Retries handled by retry_async (transient errors only)
"""
import httpx
import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple
import weakref
import config
from app.utils.retry import retry_async

logger = logging.getLogger(__name__)

# Store strong references to fire-and-forget tasks to prevent GC and ensure
# "Task exception was never retrieved" warnings are suppressed.
# Tasks auto-remove on completion via done_callback.
_background_tasks: set = set()


def _fire_and_forget(coro) -> None:
    """Schedule a coroutine as a background task with proper error handling."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            task = asyncio.create_task(coro)
            _background_tasks.add(task)

            def _task_done(t):
                _background_tasks.discard(t)
                if not t.cancelled() and t.exception():
                    logger.warning(f"Background VPN audit task failed: {t.exception()}")

            task.add_done_callback(_task_done)
    except Exception as e:
        logger.warning(f"Failed to schedule background task: {e}")

# Explicit timeout for all VPN API calls (connect, read, write, pool)
VPN_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
# Legacy float for code that expects a single number (e.g. health check)
HTTP_TIMEOUT = 10.0
MAX_RETRIES = 2
RETRY_DELAY = 1.0


class VPNAPIError(Exception):
    """Базовый класс для ошибок VPN API"""
    pass


class VPNTimeoutError(VPNAPIError):
    """Таймаут при обращении к VPN API"""
    pass


class AuthError(VPNAPIError):
    """Ошибка аутентификации (401, 403)"""
    pass


class InvalidResponseError(VPNAPIError):
    """Некорректный ответ от VPN API"""
    pass


class CriticalUUIDMismatchError(VPNAPIError):
    """Xray API returned UUID different from what we sent"""
    pass


def _validate_uuid_no_prefix(uuid_val: str) -> None:
    """Reject any UUID with environment prefix. UUID must be raw 36-char only."""
    if not uuid_val:
        return
    u = uuid_val.strip()
    if "stage-" in u or u.startswith("stage-") or "prod-" in u or u.startswith("prod-") or "test-" in u or u.startswith("test-"):
        logger.critical(f"INVALID_UUID_PREFIX_DETECTED [uuid={repr(uuid_val)[:50]}]")
        raise RuntimeError("UUID must not contain environment prefix (stage-, prod-, test-)")


def _validate_api_url_security(api_url: str) -> None:
    """Validate XRAY_API_URL: HTTPS required in PROD, no private IPs in PROD."""
    if not api_url.startswith('https://') and config.IS_PROD:
        raise ValueError(f"SECURITY: XRAY_API_URL must use HTTPS. Got: {api_url}")
    if config.IS_PROD:
        forbidden_patterns = ['127.0.0.1', 'localhost', '0.0.0.0', '172.', '192.168.', '10.']
        api_url_lower = api_url.lower()
        for pattern in forbidden_patterns:
            if pattern in api_url_lower:
                raise RuntimeError(
                    f"SECURITY: XRAY_API_URL must use public HTTPS URL, "
                    f"not private IP. Got: {api_url}"
                )


async def check_xray_health() -> bool:
    """
    Проверить доступность XRAY API через health-check endpoint.
    
    Вызывает GET /health на XRAY API сервере.
    Не бросает исключения - возвращает False при ошибках.
    
    Returns:
        True если XRAY API доступен и отвечает, False в противном случае
    """
    if not config.VPN_ENABLED:
        return False
    
    if not config.XRAY_API_URL or not config.XRAY_API_KEY:
        return False
    
    api_url = config.XRAY_API_URL.rstrip('/')
    health_url = f"{api_url}/health"
    
    try:
        headers = {"X-API-Key": config.XRAY_API_KEY}
        async with httpx.AsyncClient(timeout=VPN_HTTP_TIMEOUT) as client:
            response = await client.get(health_url, headers=headers)
            if response.status_code == 200:
                logger.debug("XRAY health check: SUCCESS")
                return True
            else:
                logger.warning(f"XRAY health check: FAILED [status={response.status_code}]")
                return False
    except Exception as e:
        logger.debug(f"XRAY health check: FAILED [error={str(e)}]")
        return False


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


async def ensure_user_in_xray(telegram_id: int, uuid: Optional[str], subscription_end: datetime, tariff: str = "basic") -> Optional[str]:
    """
    Sync user to Xray. DB is source of truth for UUID.
    1. If user has UUID: try update_user. If 404 → add_user with SAME uuid, return it.
    2. If user has no UUID: cannot add (caller must generate and pass uuid).
    Returns effective uuid (same as input) or None on failure (best-effort).
    """
    uuid_clean = str(uuid).strip() if uuid and str(uuid).strip() else None
    uuid_preview = f"{uuid_clean[:8]}..." if uuid_clean and len(uuid_clean) > 8 else (uuid_clean or "N/A")

    if uuid_clean:
        _validate_uuid_no_prefix(uuid_clean)
        logger.info(f"XRAY_UPDATE uuid={uuid_preview}")
        try:
            await update_vless_user(uuid=uuid_clean, subscription_end=subscription_end)
            logger.info("XRAY_UPDATE_SUCCESS")
            return uuid_clean
        except InvalidResponseError as e:
            if "Client not found" not in str(e) and "client not found" not in str(e).lower():
                raise
            logger.warning(f"XRAY_UPDATE_404_RECOVERY uuid={uuid_preview} → add_user (same UUID)")

    # Update 404 fallback — add_user with SAME uuid (DB is source of truth)
    if not uuid_clean:
        logger.error("ensure_user_in_xray: cannot add without uuid; DB is source of truth")
        return None
    try:
        await add_vless_user(
            telegram_id=telegram_id,
            subscription_end=subscription_end,
            uuid=uuid_clean,
            tariff=tariff,
        )
        logger.info(f"XRAY_UPDATE_FALLBACK_ADD uuid={uuid_preview} tariff={tariff}")
        return uuid_clean
    except Exception as add_e:
        logger.critical(f"XRAY_ADD_FAILED uuid={uuid_preview} error={add_e}", exc_info=True)
    return None


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
    """
    Remove UUID from Xray with retry for orphan cleanup.
    Used when Phase 2 (DB tx) fails after Phase 1 (add_vless_user) succeeded.

    Retries on: httpx.HTTPError, httpx.TimeoutException, ConnectionError, OSError.
    Exponential backoff: 1s → 2s → 4s.
    If all retries fail → raises (caller must not suppress).
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
            logger.info("ORPHAN_CLEANUP_SUCCESS", extra={"uuid": uuid_preview})
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


async def reissue_vpn_access(old_uuid: str, telegram_id: int, subscription_end: datetime) -> Tuple[str, str]:
    """
    Перевыпустить VPN доступ: удалить старый UUID и создать новый.
    
    КРИТИЧЕСКИ ВАЖНО:
    - Если add-user упал → НЕ удалять старый UUID (он уже удалён)
    - Если remove-user упал → прервать операцию, старый UUID остаётся
    - Все шаги логируются
    
    Args:
        old_uuid: Старый UUID для удаления
        telegram_id: Telegram ID пользователя
        subscription_end: Дата окончания подписки (expiryTime для нового ключа)
    
    Returns:
        Новый UUID (str)
    
    Raises:
        VPNAPIError: При ошибках VPN API
        ValueError: При некорректных параметрах
    """
    if not old_uuid or not old_uuid.strip():
        error_msg = "Invalid old_uuid provided for reissue"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    old_uuid_clean = old_uuid.strip()
    uuid_preview = f"{old_uuid_clean[:8]}..." if old_uuid_clean and len(old_uuid_clean) > 8 else (old_uuid_clean or "N/A")
    
    logger.info(f"VPN key reissue: START [action=reissue, old_uuid={uuid_preview}]")
    
    # ШАГ 1: Удаляем старый UUID
    try:
        await remove_vless_user(old_uuid_clean)
        logger.info(f"VPN key reissue: OLD_UUID_REMOVED [old_uuid={uuid_preview}]")
    except Exception as e:
        error_msg = f"Failed to remove old UUID during reissue: {str(e)}"
        logger.error(f"VPN key reissue: REMOVE_FAILED [old_uuid={uuid_preview}, error={error_msg}]")
        # КРИТИЧНО: Если не удалось удалить старый UUID - прерываем операцию
        raise VPNAPIError(error_msg) from e
    
    # ШАГ 2: Generate UUID for API; Xray response overrides (Xray is source of truth)
    import database
    new_uuid = database._generate_subscription_uuid()
    try:
        vless_result = await add_vless_user(
            telegram_id=telegram_id,
            subscription_end=subscription_end,
            uuid=new_uuid
        )
        uuid_from_api = vless_result.get("uuid")
        vless_url = vless_result.get("vless_url")
        if not uuid_from_api:
            error_msg = "VPN API returned empty UUID during reissue"
            logger.error(f"VPN key reissue: ADD_FAILED [error={error_msg}]")
            raise VPNAPIError(error_msg)
        if not vless_url:
            error_msg = "VPN API did not return vless_link during reissue"
            logger.error(f"VPN key reissue: ADD_FAILED [error={error_msg}]")
            raise VPNAPIError(error_msg)
        new_uuid = uuid_from_api  # HARD OVERRIDE — Xray is source of truth

        new_uuid_preview = f"{new_uuid[:8]}..." if new_uuid and len(new_uuid) > 8 else (new_uuid or "N/A")
        logger.info(f"VPN key reissue: SUCCESS [old_uuid={uuid_preview}, new_uuid={new_uuid_preview}]")

        return new_uuid, vless_url
        
    except Exception as e:
        error_msg = f"Failed to create new UUID during reissue: {str(e)}"
        logger.error(f"VPN key reissue: ADD_FAILED [error={error_msg}]")
        # КРИТИЧНО: Если не удалось создать новый UUID - старый уже удалён
        # Это критическая ситуация, но мы не можем восстановить старый UUID
        raise VPNAPIError(error_msg) from e


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
