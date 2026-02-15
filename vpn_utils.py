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
from typing import Dict, Optional
import config
from app.utils.retry import retry_async
from app.core.metrics import get_metrics, timer
from app.core.cost_model import get_cost_model, CostCenter

logger = logging.getLogger(__name__)

# Explicit timeout for all VPN API calls (connect, read, write, pool)
VPN_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
# Legacy float for code that expects a single number (e.g. health check)
HTTP_TIMEOUT = 10.0
MAX_RETRIES = 2
RETRY_DELAY = 1.0


class VPNAPIError(Exception):
    """Базовый класс для ошибок VPN API"""
    pass


class TimeoutError(VPNAPIError):
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
        async with httpx.AsyncClient(timeout=VPN_HTTP_TIMEOUT) as client:
            response = await client.get(health_url)
            if response.status_code == 200:
                logger.debug("XRAY health check: SUCCESS")
                return True
            else:
                logger.warning(f"XRAY health check: FAILED [status={response.status_code}]")
                return False
    except Exception as e:
        logger.debug(f"XRAY health check: FAILED [error={str(e)}]")
        return False


async def add_vless_user(telegram_id: int, subscription_end: datetime, uuid: Optional[str] = None) -> Dict[str, str]:
    """
    Создать нового пользователя VLESS в Xray Core.

    INVARIANT: Must NEVER be called inside an active DB transaction (orphan UUID risk).
    Callers that hold a DB transaction MUST use two-phase: call add_vless_user OUTSIDE the tx,
    then pass the returned {uuid, vless_url} as pre_provisioned_uuid to grant_access.
    
    Вызывает POST /add-user на локальном FastAPI VPN API сервере.
    Передаёт telegram_id и expiry_timestamp_ms (subscription_end в мс).
    API возвращает uuid и vless_link.
    
    UUID is stored and sent as-is (no prefix). Stage isolation uses X-Inbound-Tag header.
    
    Args:
        telegram_id: Telegram ID пользователя
        subscription_end: Дата окончания подписки (используется как expiryTime в Xray)
        uuid: Optional. If provided, sent to API; else Xray generates. Returned UUID is canonical.
    
    Returns:
        Словарь с ключами:
        - "uuid": UUID пользователя (str, raw 36-char UUID)
        - "vless_url": VLESS URL для подключения (str, from API only — never generated locally)
    
    Raises:
        ValueError: Если XRAY_API_URL или XRAY_API_KEY не настроены
        httpx.HTTPError: При ошибках сети
        httpx.HTTPStatusError: При ошибках HTTP (4xx, 5xx)
        Exception: При других ошибках
    """
    # Проверяем feature flag
    if not config.VPN_PROVISIONING_ENABLED:
        error_msg = "VPN provisioning is disabled (VPN_PROVISIONING_ENABLED=false)"
        logger.warning(error_msg)
        raise ValueError(error_msg)
    
    # Проверяем доступность VPN API
    if not config.VPN_ENABLED:
        error_msg = (
            "VPN API is not configured. "
            "Please set XRAY_API_URL and XRAY_API_KEY environment variables. "
            "VPN operations are blocked until configuration is complete."
        )
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    if not config.XRAY_API_URL:
        error_msg = "XRAY_API_URL environment variable is not set"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    if not config.XRAY_API_KEY:
        error_msg = "XRAY_API_KEY environment variable is not set"
        logger.error(error_msg)
        raise ValueError(error_msg)

    if uuid and str(uuid).strip():
        _validate_uuid_no_prefix(uuid)

    # STAGE изоляция: проверяем окружение
    if config.IS_STAGE:
        logger.info("XRAY_CALL_START [operation=add_user, environment=stage]")
    elif config.IS_PROD:
        logger.info("XRAY_CALL_START [operation=add_user, environment=prod]")
    else:
        logger.info("XRAY_CALL_START [operation=add_user, environment=local]")
    
    # Проверяем что URL правильный и не является private IP
    api_url = config.XRAY_API_URL.rstrip('/')
    if not api_url.startswith('https://'):
        error_msg = f"SECURITY: XRAY_API_URL must use HTTPS. Got: {api_url}"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    # КРИТИЧЕСКАЯ ПРОВЕРКА: Запрещаем использование private IP адресов
    forbidden_patterns = ['127.0.0.1', 'localhost', '0.0.0.0', '172.', '192.168.', '10.']
    api_url_lower = api_url.lower()
    for pattern in forbidden_patterns:
        if pattern in api_url_lower:
            error_msg = (
                f"SECURITY: XRAY_API_URL must use public HTTPS URL (Cloudflare Tunnel), "
                f"not private IP. Got: {api_url}. "
                f"Expected format: https://api.mynewllcw.com"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)

    # STEP 6 — F2: CIRCUIT BREAKER LITE
    # Check circuit breaker before making VPN API call
    from app.core.circuit_breaker import get_circuit_breaker
    vpn_breaker = get_circuit_breaker("vpn_api")
    if vpn_breaker.should_skip():
        # Circuit breaker is OPEN - skip operation
        # This is logged by should_skip() (throttled)
        raise VPNAPIError("VPN API circuit breaker is OPEN")
    
    assert subscription_end.tzinfo is not None, "subscription_end must be timezone-aware"
    assert subscription_end.tzinfo == timezone.utc, "subscription_end must be UTC"
    expiry_ms = int(subscription_end.timestamp() * 1000)
    url = f"{api_url}/add-user"
    headers = {
        "X-API-Key": config.XRAY_API_KEY,
        "Content-Type": "application/json"
    }
    
    # STAGE изоляция: добавляем заголовок для отдельного inbound/tag
    if config.IS_STAGE:
        headers["X-Environment"] = "stage"
        headers["X-Inbound-Tag"] = "stage"
    
    json_body: dict = {
        "telegram_id": telegram_id,
        "expiry_timestamp_ms": expiry_ms,
    }
    if not uuid or not str(uuid).strip():
        raise ValueError("add_vless_user requires uuid; DB is source of truth")
    json_body["uuid"] = str(uuid).strip()
    logger.info(f"UUID_AUDIT_ADD_REQUEST [uuid_sent_to_api={repr(json_body['uuid'])}]")
    
    # Логируем начало операции
    logger.info(
        f"vpn_api add_user: START [url={url}, telegram_id={telegram_id}, "
        f"expiry_timestamp_ms={expiry_ms}, subscription_end={subscription_end.isoformat()}]"
    )
    
    # Use centralized retry utility for HTTP calls (only retries transient errors)
    async def _make_request():
        async with httpx.AsyncClient(timeout=VPN_HTTP_TIMEOUT) as client:
            logger.debug("vpn_api add_user: HTTP_REQUEST")
            response = await client.post(url, headers=headers, json=json_body)
            
            # Log response
            response_text_preview = response.text[:200] if response.text else "empty"
            logger.info(
                f"vpn_api add_user: RESPONSE [status={response.status_code}, "
                f"response_preview={response_text_preview}]"
            )
            
            # Check for auth errors (should NOT be retried - domain exception)
            if response.status_code == 401 or response.status_code == 403:
                error_msg = f"Authentication error: status={response.status_code}, response={response.text[:200]}"
                logger.error(f"vpn_api add_user: AUTH_ERROR [{error_msg}]")
                raise AuthError(error_msg)
            
            # Convert 4xx to domain exception (should NOT be retried)
            if 400 <= response.status_code < 500:
                error_msg = f"Client error: status={response.status_code}, response={response.text[:200]}"
                logger.error(f"vpn_api add_user: CLIENT_ERROR [{error_msg}]")
                raise InvalidResponseError(error_msg)
            
            # Only 5xx/timeout/network errors will be retried
            response.raise_for_status()
            return response
    
    # C1.1 - METRICS: Measure VPN API latency
    with timer("vpn_api_latency_ms"):
        try:
            response = await retry_async(
                _make_request,
                retries=MAX_RETRIES,
                base_delay=RETRY_DELAY,
                max_delay=5.0,
                retry_on=(httpx.HTTPError, httpx.TimeoutException, ConnectionError, OSError)
            )
            
            # C1.1 - METRICS: Track retries
            metrics = get_metrics()
            metrics.increment_counter("retries_total", value=MAX_RETRIES)
            
            # D2.1 - COST CENTERS: Track VPN API call cost
            cost_model = get_cost_model()
            cost_model.record_cost(CostCenter.VPN_API_CALLS, cost_units=1.0)
            cost_model.record_cost(CostCenter.EXTERNAL_API_CALLS, cost_units=1.0)
            if MAX_RETRIES > 0:
                cost_model.record_cost(CostCenter.RETRIES, cost_units=MAX_RETRIES)
            
            # STEP 4 — PART D: EXTERNAL DEPENDENCY SANDBOXING
            # Parse JSON response (API returns uuid and vless_link)
            # Treat all external responses as untrusted and possibly malformed
            try:
                data = response.json()
            except Exception as e:
                error_msg = f"Invalid JSON response: {response.text[:200]}"
                logger.error(f"vpn_api add_user: INVALID_JSON [{error_msg}]")
                raise InvalidResponseError(error_msg) from e
            
            # STEP 4 — PART D: EXTERNAL DEPENDENCY SANDBOXING
            # Validate response schema - only allow expected fields
            if not isinstance(data, dict):
                error_msg = f"Invalid response type: expected dict, got {type(data)}"
                logger.error(f"vpn_api add_user: INVALID_RESPONSE_TYPE [{error_msg}]")
                raise InvalidResponseError(error_msg)
            
            # Strict schema: API must return uuid and vless_link (no local fallback)
            required_fields = {"uuid", "vless_link"}
            if not required_fields.issubset(data.keys()):
                raise InvalidResponseError(
                    f"Invalid API response. Expected fields: {required_fields}, got: {set(data.keys())}"
                )

            returned_uuid = data.get("uuid")
            vless_link = data.get("vless_link")

            if not returned_uuid:
                error_msg = f"Invalid response from Xray API: missing 'uuid'. Response: {data}"
                logger.error(f"vpn_api add_user: INVALID_RESPONSE [{error_msg}]")
                raise InvalidResponseError(error_msg)

            returned_uuid = str(returned_uuid).strip()
            # Xray is source of truth: always trust returned UUID (no mismatch assertion)
            logger.info(f"XRAY_SOURCE_OF_TRUTH uuid={returned_uuid}")

            if not vless_link:
                raise InvalidResponseError(
                    "Xray API did not return vless_link. "
                    "Local fallback generation is forbidden by architecture."
                )

            vless_url = vless_link

            uuid_preview = f"{returned_uuid[:8]}..." if len(returned_uuid) > 8 else returned_uuid
            logger.info(f"XRAY_ADD uuid={uuid_preview} status=200")
            logger.info(f"XRAY_CALL_SUCCESS [operation=add_user, uuid={uuid_preview}, environment={config.APP_ENV}]")

            try:
                import database
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(
                        database._log_vpn_lifecycle_audit_async(
                            action="vpn_add_user",
                            telegram_id=0,
                            uuid=returned_uuid,
                            source=None,
                            result="success",
                            details="UUID from Xray (source of truth)"
                        )
                    )
            except Exception as e:
                logger.warning(f"Failed to log VPN add_user audit (non-blocking): {e}")

            return {
                "uuid": returned_uuid,
                "vless_url": vless_url
            }
            
        except (AuthError, InvalidResponseError) as e:
            # Domain exceptions should NOT be retried - raise immediately
            # STEP 6 — F2: CIRCUIT BREAKER LITE
            # Don't record failure for domain errors (not transient)
            logger.error(f"XRAY_CALL_FAILED [operation=add_user, error_type=domain_error, environment={config.APP_ENV}, error={str(e)[:100]}]")
            raise
        except Exception as e:
            # All other exceptions are wrapped by retry_async or are unexpected
            # STEP 6 — F2: CIRCUIT BREAKER LITE
            # Record failure for transient errors
            vpn_breaker.record_failure()
            error_msg = f"Failed to create VLESS user: {e}"
            logger.error(f"XRAY_CALL_FAILED [operation=add_user, error_type=transient_error, environment={config.APP_ENV}, error={error_msg[:100]}]")
            raise VPNAPIError(error_msg) from e


async def ensure_user_in_xray(telegram_id: int, uuid: Optional[str], subscription_end: datetime) -> Optional[str]:
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
            uuid=uuid_clean
        )
        logger.info(f"XRAY_UPDATE_FALLBACK_ADD uuid={uuid_preview}")
        return uuid_clean
    except Exception as add_e:
        logger.critical(f"XRAY_ADD_FAILED uuid={uuid_preview} error={add_e}", exc_info=True)
    return None


async def update_vless_user(uuid: str, subscription_end: datetime) -> None:
    """
    Обновить expiryTime существующего клиента в Xray Core.
    
    Вызывает POST /update-user на Xray API сервере.
    Используется при продлении подписки — UUID остаётся, обновляется только срок.
    
    UUID is sent exactly as stored (no transformation).
    
    Args:
        uuid: UUID пользователя для обновления
        subscription_end: Новая дата окончания подписки
    
    Raises:
        ValueError: Если конфигурация неверна
        httpx.HTTPStatusError: При 404 (клиент не найден) или других HTTP ошибках
    """
    if not config.VPN_PROVISIONING_ENABLED:
        error_msg = "VPN provisioning is disabled (VPN_PROVISIONING_ENABLED=false)"
        logger.warning(error_msg)
        raise ValueError(error_msg)
    
    if not config.VPN_ENABLED or not config.XRAY_API_URL or not config.XRAY_API_KEY:
        error_msg = "VPN API is not configured"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    if not uuid or not str(uuid).strip():
        error_msg = f"Invalid UUID provided: {uuid}"
        logger.error(error_msg)
        raise ValueError(error_msg)
    _validate_uuid_no_prefix(uuid)
    assert subscription_end.tzinfo is not None, "subscription_end must be timezone-aware"
    assert subscription_end.tzinfo == timezone.utc, "subscription_end must be UTC"
    uuid_clean = str(uuid).strip()
    logger.info(f"UUID_AUDIT_UPDATE_REQUEST [uuid={repr(uuid_clean)}]")
    
    # SECURITY: XRAY_API_URL must be HTTPS, no private IP
    api_url = config.XRAY_API_URL.rstrip('/')
    if not api_url.startswith('https://'):
        error_msg = f"SECURITY: XRAY_API_URL must use HTTPS. Got: {api_url}"
        logger.error(error_msg)
        raise ValueError(error_msg)
    forbidden_patterns = ['127.0.0.1', 'localhost', '0.0.0.0', '172.', '192.168.', '10.']
    api_url_lower = api_url.lower()
    for pattern in forbidden_patterns:
        if pattern in api_url_lower:
            error_msg = (
                f"SECURITY: XRAY_API_URL must use public HTTPS URL (Cloudflare Tunnel), "
                f"not private IP. Got: {api_url}. Expected format: https://api.mynewllcw.com"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)
    
    expiry_ms = int(subscription_end.timestamp() * 1000)
    url = f"{api_url}/update-user"
    headers = {
        "X-API-Key": config.XRAY_API_KEY,
        "Content-Type": "application/json"
    }
    if config.IS_STAGE:
        headers["X-Environment"] = "stage"
        headers["X-Inbound-Tag"] = "stage"
    
    json_body = {
        "uuid": uuid_clean,
        "expiry_timestamp_ms": expiry_ms
    }
    
    logger.info(
        f"vpn_api update_user: START [uuid={uuid_clean[:8]}..., "
        f"expiry_timestamp_ms={expiry_ms}, subscription_end={subscription_end.isoformat()}]"
    )
    
    from app.core.circuit_breaker import get_circuit_breaker
    vpn_breaker = get_circuit_breaker("vpn_api")
    if vpn_breaker.should_skip():
        raise VPNAPIError("VPN API circuit breaker is OPEN")
    
    try:
        async with httpx.AsyncClient(timeout=VPN_HTTP_TIMEOUT) as client:
            response = await client.post(url, headers=headers, json=json_body)
            if response.status_code == 404:
                error_msg = f"Client not found in Xray: {uuid_clean[:8]}..."
                logger.error(f"vpn_api update_user: NOT_FOUND [{error_msg}]")
                raise InvalidResponseError(error_msg)
            response.raise_for_status()
        
        logger.info(f"vpn_api update_user: SUCCESS [uuid={uuid_clean[:8]}...]")
    except (AuthError, InvalidResponseError):
        raise
    except Exception as e:
        vpn_breaker.record_failure()
        raise VPNAPIError(f"Failed to update VLESS user expiry: {e}") from e


async def remove_vless_user(uuid: str) -> None:
    """
    Удалить пользователя VLESS из Xray Core.
    
    Вызывает POST /remove-user на Xray API сервере для удаления пользователя.
    
    UUID is sent exactly as stored (no transformation).
    
    Args:
        uuid: UUID пользователя для удаления (str, raw 36-char UUID)
    
    Raises:
        ValueError: Если XRAY_API_URL или XRAY_API_KEY не настроены, или uuid пустой
        httpx.HTTPError: При ошибках сети
        httpx.HTTPStatusError: При ошибках HTTP (4xx, 5xx)
        Exception: При других ошибках
    
    Note:
        Функция НЕ игнорирует ошибки. Если удаление не удалось,
        будет выброшено исключение.
    """
    # Проверяем feature flag
    if not config.VPN_PROVISIONING_ENABLED:
        error_msg = "VPN provisioning is disabled (VPN_PROVISIONING_ENABLED=false)"
        logger.warning(error_msg)
        raise ValueError(error_msg)
    
    # Проверяем доступность VPN API
    if not config.VPN_ENABLED:
        error_msg = (
            f"VPN API is not configured. Cannot remove UUID {uuid}. "
            "Please set XRAY_API_URL and XRAY_API_KEY environment variables."
        )
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    if not config.XRAY_API_URL:
        error_msg = "XRAY_API_URL environment variable is not set"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    if not config.XRAY_API_KEY:
        error_msg = "XRAY_API_KEY environment variable is not set"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    if not uuid or not str(uuid).strip():
        error_msg = f"Invalid UUID provided: {uuid}"
        logger.error(error_msg)
        raise ValueError(error_msg)
    _validate_uuid_no_prefix(uuid)
    uuid_clean = str(uuid).strip()
    if config.IS_STAGE:
        logger.info(f"XRAY_CALL_START [operation=remove_user, uuid={uuid_clean[:8]}..., environment=stage]")
    elif config.IS_PROD:
        logger.info(f"XRAY_CALL_START [operation=remove_user, uuid={uuid_clean[:8]}..., environment=prod]")
    else:
        logger.info(f"XRAY_CALL_START [operation=remove_user, uuid={uuid_clean[:8]}..., environment=local]")
    
    # SECURITY: XRAY_API_URL must be HTTPS, no private IP
    api_url = config.XRAY_API_URL.rstrip('/')
    if not api_url.startswith('https://'):
        error_msg = f"SECURITY: XRAY_API_URL must use HTTPS. Got: {api_url}"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    forbidden_patterns = ['127.0.0.1', 'localhost', '0.0.0.0', '172.', '192.168.', '10.']
    api_url_lower = api_url.lower()
    for pattern in forbidden_patterns:
        if pattern in api_url_lower:
            error_msg = (
                f"SECURITY: XRAY_API_URL must use public HTTPS URL (Cloudflare Tunnel), "
                f"not private IP. Got: {api_url}. "
                f"Expected format: https://api.mynewllcw.com"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)
    
    url = f"{api_url}/remove-user/{uuid_clean}"
    headers = {
        "X-API-Key": config.XRAY_API_KEY,
        "Content-Type": "application/json"
    }
    
    # STAGE изоляция: добавляем заголовок для отдельного inbound/tag
    if config.IS_STAGE:
        headers["X-Environment"] = "stage"
        headers["X-Inbound-Tag"] = "stage"
    
    # Безопасное логирование UUID
    uuid_preview = f"{uuid_clean[:8]}..." if uuid_clean and len(uuid_clean) > 8 else (uuid_clean or "N/A")
    logger.info(f"vpn_api remove_user: START [uuid={uuid_preview}, url={url}, environment={config.APP_ENV}]")
    
    # Use centralized retry utility for HTTP calls (only retries transient errors)
    async def _make_request():
        async with httpx.AsyncClient(timeout=VPN_HTTP_TIMEOUT) as client:
            logger.debug("vpn_api remove_user: HTTP_REQUEST")
            response = await client.post(url, headers=headers)
            
            # Log response
            response_text_preview = response.text[:200] if response.text else "empty"
            logger.info(
                f"vpn_api remove_user: RESPONSE [uuid={uuid_preview}, status={response.status_code}, "
                f"response_preview={response_text_preview}]"
            )
            
            # Check for auth errors (should NOT be retried - domain exception)
            if response.status_code == 401 or response.status_code == 403:
                error_msg = f"Authentication error: status={response.status_code}, response={response.text[:200]}"
                logger.error(f"vpn_api remove_user: AUTH_ERROR [uuid={uuid_preview}, {error_msg}]")
                raise AuthError(error_msg)
            
            # IDEMPOTENCY: 404 means UUID not found or already removed - this is NOT an error
            if response.status_code == 404:
                logger.info(
                    f"vpn_api remove_user: UUID_NOT_FOUND [uuid={uuid_preview}, status=404] - "
                    "UUID already removed or never existed (idempotent operation)"
                )
                # UUID already removed - successful operation (idempotency)
                try:
                    import database
                    import asyncio
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.create_task(
                            database._log_vpn_lifecycle_audit_async(
                                action="vpn_remove_user",
                                telegram_id=0,
                                uuid=uuid_clean,
                                source=None,
                                result="success",
                                details="UUID already removed (idempotent)"
                            )
                        )
                except Exception as e:
                    logger.warning(f"Failed to log VPN remove_user audit (non-blocking): {e}")
                # Return special marker for 404 (idempotent success)
                return response
            
            # Convert 4xx to domain exception (should NOT be retried)
            if 400 <= response.status_code < 500:
                error_msg = f"Client error: status={response.status_code}, response={response.text[:200]}"
                logger.error(f"vpn_api remove_user: CLIENT_ERROR [uuid={uuid_preview}, {error_msg}]")
                raise InvalidResponseError(error_msg)
            
            # Only 5xx/timeout/network errors will be retried
            response.raise_for_status()
            return response
    
    # C1.1 - METRICS: Measure VPN API latency
    with timer("vpn_api_latency_ms"):
        try:
            response = await retry_async(
                _make_request,
                retries=MAX_RETRIES,
                base_delay=RETRY_DELAY,
                max_delay=5.0,
                retry_on=(httpx.HTTPError, httpx.TimeoutException, ConnectionError, OSError)
            )
            
            # C1.1 - METRICS: Track retries
            metrics = get_metrics()
            metrics.increment_counter("retries_total", value=MAX_RETRIES)
            
            # D2.1 - COST CENTERS: Track VPN API call cost
            cost_model = get_cost_model()
            cost_model.record_cost(CostCenter.VPN_API_CALLS, cost_units=1.0)
            cost_model.record_cost(CostCenter.EXTERNAL_API_CALLS, cost_units=1.0)
            if MAX_RETRIES > 0:
                cost_model.record_cost(CostCenter.RETRIES, cost_units=MAX_RETRIES)
            
            # If we got here and response is 404, it was handled in _make_request
            if response.status_code == 404:
                logger.info(f"XRAY_CALL_SUCCESS [operation=remove_user, uuid={uuid_preview}, environment={config.APP_ENV}, status=idempotent_404]")
                return
            
            logger.info(f"XRAY_CALL_SUCCESS [operation=remove_user, uuid={uuid_preview}, environment={config.APP_ENV}]")
            
            # VPN AUDIT LOG: Log successful UUID removal (non-blocking)
            # Note: Full audit log will be written by caller with correct telegram_id and source
            try:
                import database
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(
                        database._log_vpn_lifecycle_audit_async(
                            action="vpn_remove_user",
                            telegram_id=0,  # Will be updated by caller
                            uuid=uuid_clean,
                            source=None,  # Will be updated by caller
                            result="success",
                            details="UUID removed via VPN API"
                        )
                    )
            except Exception as e:
                logger.warning(f"Failed to log VPN remove_user audit (non-blocking): {e}")
            
            return
            
        except (AuthError, ValueError) as e:
            # Domain exceptions should NOT be retried - raise immediately
            logger.error(f"XRAY_CALL_FAILED [operation=remove_user, error_type=domain_error, uuid={uuid_preview}, environment={config.APP_ENV}, error={str(e)[:100]}]")
            raise
        except Exception as e:
            # All other exceptions are wrapped by retry_async or are unexpected
            error_msg = f"Failed to remove VLESS user: {e}"
            logger.error(f"XRAY_CALL_FAILED [operation=remove_user, error_type=transient_error, uuid={uuid_preview}, environment={config.APP_ENV}, error={error_msg[:100]}]")
            raise VPNAPIError(error_msg) from e


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


async def reissue_vpn_access(old_uuid: str, telegram_id: int, subscription_end: datetime) -> str:
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
# DEPRECATED: Legacy file-based functions (kept for backward compatibility)
# ============================================================================

def has_free_vpn_keys() -> bool:
    """
    DEPRECATED: Функция проверки наличия VPN-ключей в файле.
    
    Больше не используется. VPN-ключи создаются динамически через Xray API.
    Всегда возвращает True для обратной совместимости.
    
    Returns:
        True (для обратной совместимости)
    """
    logger.warning("has_free_vpn_keys() is deprecated. VPN keys are created dynamically via Xray API.")
    return True


def get_free_vpn_key() -> str:
    """
    DEPRECATED: Функция получения VPN-ключа из файла.
    
    Больше не используется. VPN-ключи создаются динамически через Xray API.
    Вызывает исключение при вызове.
    
    Raises:
        ValueError: Всегда, так как эта функция устарела
    """
    error_msg = (
        "get_free_vpn_key() is deprecated. "
        "Use add_vless_user() to create VPN keys dynamically via Xray API."
    )
    logger.error(error_msg)
    raise ValueError(error_msg)
