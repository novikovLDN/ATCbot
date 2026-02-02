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
from typing import Dict, Optional
from urllib.parse import quote
import config
from app.utils.retry import retry_async
from app.core.metrics import get_metrics, timer
from app.core.cost_model import get_cost_model, CostCenter

logger = logging.getLogger(__name__)

# HTTP клиент с таймаутами для API запросов
# Используем XRAY_API_TIMEOUT из config (default 5s), но не менее 3s для надежности
HTTP_TIMEOUT = max(float(config.XRAY_API_TIMEOUT) if hasattr(config, 'XRAY_API_TIMEOUT') else 5.0, 3.0)
MAX_RETRIES = 2  # Количество повторных попыток при ошибке (2 retry = 3 попытки всего)
RETRY_DELAY = 1.0  # Задержка между попытками в секундах (backoff будет: 1s, 2s)


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
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
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


def validate_vless_link(vless_link: str) -> bool:
    """
    Валидирует VLESS ссылку на наличие запрещённых параметров.
    
    Защита от регресса конфигурации:
    - Проверяет что строка НЕ содержит "flow="
    
    Args:
        vless_link: VLESS URL строка для проверки
    
    Returns:
        True если ссылка валидна (не содержит flow=), False в противном случае
    
    Raises:
        ValueError: Если vless_link пустая или None
    """
    if not vless_link or not isinstance(vless_link, str):
        raise ValueError(f"Invalid vless_link: must be non-empty string, got: {vless_link}")
    
    # Проверяем наличие запрещённого параметра flow
    if "flow=" in vless_link:
        logger.error(
            f"validate_vless_link: REGRESSION_DETECTED [vless_link_preview={vless_link[:100]}...] - "
            "contains forbidden 'flow=' parameter"
        )
        return False
    
    return True


def generate_vless_url(uuid: str) -> str:
    """
    Генерирует VLESS URL для подключения к Xray Core серверу.
    
    КРИТИЧЕСКИ ВАЖНО: Параметр flow ЗАПРЕЩЁН для REALITY протокола.
    REALITY несовместим с XTLS flow (xtls-rprx-vision).
    Добавление flow приведёт к ошибкам подключения.
    
    Формат (БЕЗ flow параметра):
    vless://UUID@SERVER_IP:PORT
    ?encryption=none
    &security=reality
    &type=tcp
    &sni={REALITY_SNI}
    &fp=ios
    &pbk={REALITY_PBK}
    &sid={REALITY_SID}
    #AtlasSecure
    
    Args:
        uuid: UUID пользователя
    
    Returns:
        VLESS URL строка (БЕЗ flow параметра)
    """
    # Кодируем параметры для URL
    server_address = f"{uuid}@{config.XRAY_SERVER_IP}:{config.XRAY_PORT}"
    
    # Параметры запроса (БЕЗ flow - flow ЗАПРЕЩЁН для REALITY)
    # REALITY протокол не использует flow, так как несовместим с XTLS
    params = {
        "encryption": "none",
        "security": "reality",
        "type": "tcp",
        "sni": config.XRAY_SNI,
        "fp": config.XRAY_FP,
        "pbk": config.XRAY_PUBLIC_KEY,
        "sid": config.XRAY_SHORT_ID
    }
    
    # Формируем query string
    query_parts = [f"{key}={quote(str(value))}" for key, value in params.items()]
    query_string = "&".join(query_parts)
    
    # Формируем полный URL
    fragment = "🇪🇺 Atlas Secure ⚡️"
    vless_url = f"vless://{server_address}?{query_string}#{quote(fragment)}"
    
    # ЗАЩИТА ОТ РЕГРЕССА: Валидируем сгенерированную ссылку
    if not validate_vless_link(vless_url):
        error_msg = (
            f"REGRESSION: Generated VLESS URL contains forbidden 'flow=' parameter. "
            f"This should never happen. UUID: {uuid[:8]}..."
        )
        logger.error(f"generate_vless_url: {error_msg}")
        raise ValueError(error_msg)
    
    return vless_url


async def add_vless_user() -> Dict[str, str]:
    """
    Создать нового пользователя VLESS в Xray Core.
    
    Вызывает POST /add-user на локальном FastAPI VPN API сервере.
    API возвращает только UUID, а VLESS URL генерируется локально.
    
    В STAGE окружении UUID получает префикс "stage-" для изоляции.
    
    Returns:
        Словарь с ключами:
        - "uuid": UUID пользователя (str, с префиксом "stage-" в STAGE)
        - "vless_url": VLESS URL для подключения (str, сгенерирован локально)
    
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
    
    # STAGE изоляция: проверяем окружение
    if config.IS_STAGE:
        logger.info("XRAY_CALL_START [operation=add_user, environment=stage]")
    elif config.IS_PROD:
        logger.info("XRAY_CALL_START [operation=add_user, environment=prod]")
    else:
        logger.info("XRAY_CALL_START [operation=add_user, environment=local]")
    
    # Проверяем что URL правильный и не является private IP
    api_url = config.XRAY_API_URL.rstrip('/')
    if not api_url.startswith('http://') and not api_url.startswith('https://'):
        error_msg = f"Invalid XRAY_API_URL format: {api_url}. Must start with http:// or https://"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    # КРИТИЧЕСКАЯ ПРОВЕРКА: Запрещаем использование private IP адресов
    # FastAPI работает только на 127.0.0.1:8000, доступ через Cloudflare Tunnel
    forbidden_patterns = ['127.0.0.1', 'localhost', '0.0.0.0', '172.', '192.168.', '10.']
    api_url_lower = api_url.lower()
    for pattern in forbidden_patterns:
        if pattern in api_url_lower:
            error_msg = (
                f"SECURITY: XRAY_API_URL must use public HTTPS URL (Cloudflare Tunnel), "
                f"not private IP. Got: {api_url}. "
                f"Expected format: https://api.myvpncloud.net"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)
    
    # Должен быть HTTPS для безопасности
    if not api_url.startswith('https://'):
        logger.warning(f"XRAY_API_URL uses HTTP instead of HTTPS: {api_url}. Consider using HTTPS for security.")
    
    # STEP 6 — F2: CIRCUIT BREAKER LITE
    # Check circuit breaker before making VPN API call
    from app.core.circuit_breaker import get_circuit_breaker
    vpn_breaker = get_circuit_breaker("vpn_api")
    if vpn_breaker.should_skip():
        # Circuit breaker is OPEN - skip operation
        # This is logged by should_skip() (throttled)
        raise VPNAPIError("VPN API circuit breaker is OPEN")
    
    url = f"{api_url}/add-user"
    headers = {
        "X-API-Key": config.XRAY_API_KEY,
        "Content-Type": "application/json"
    }
    
    # STAGE изоляция: добавляем заголовок для отдельного inbound/tag
    if config.IS_STAGE:
        headers["X-Environment"] = "stage"
        headers["X-Inbound-Tag"] = "stage"
    
    # Логируем начало операции
    logger.info(f"vpn_api add_user: START [url={url}, environment={config.APP_ENV}]")
    
    # Use centralized retry utility for HTTP calls (only retries transient errors)
    async def _make_request():
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            logger.debug("vpn_api add_user: HTTP_REQUEST")
            response = await client.post(url, headers=headers)
            
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
            
            # Validate response structure
            uuid = data.get("uuid")
            vless_link = data.get("vless_link")
            
            if not uuid:
                error_msg = f"Invalid response from Xray API: missing 'uuid'. Response: {data}"
                logger.error(f"vpn_api add_user: INVALID_RESPONSE [{error_msg}]")
                raise InvalidResponseError(error_msg)
            
            # STAGE изоляция: добавляем префикс к UUID
            original_uuid = str(uuid)
            if config.IS_STAGE:
                # В STAGE добавляем префикс для изоляции
                uuid = f"stage-{original_uuid}"
                logger.info(f"XRAY_CALL_STAGE_ISOLATION [original_uuid={original_uuid[:8]}..., prefixed_uuid={uuid[:14]}...]")
            
            # Use vless_link from API response if available, otherwise generate locally
            if vless_link:
                vless_url = vless_link
            else:
                # Generate VLESS URL locally based on UUID + server constants (fallback)
                # Используем оригинальный UUID для генерации URL (XRAY API работает с оригинальным UUID)
                vless_url = generate_vless_url(original_uuid)
            
            # Safe UUID logging (first 8 characters only)
            uuid_preview = f"{uuid[:8]}..." if uuid and len(uuid) > 8 else (uuid or "N/A")
            logger.info(f"XRAY_CALL_SUCCESS [operation=add_user, uuid={uuid_preview}, environment={config.APP_ENV}]")
            
            # VPN AUDIT LOG: Log successful UUID creation (non-blocking)
            try:
                import database
                # Create async task for logging (don't block main flow)
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(
                        database._log_vpn_lifecycle_audit_async(
                            action="vpn_add_user",
                            telegram_id=0,  # Will be updated by caller with real telegram_id
                            uuid=str(uuid),
                            source=None,  # Will be updated by caller
                            result="success",
                            details="UUID created via VPN API"
                        )
                    )
            except Exception as e:
                logger.warning(f"Failed to log VPN add_user audit (non-blocking): {e}")
            
            return {
                "uuid": str(uuid),
                "vless_url": vless_url
            }
            
        except (AuthError, InvalidResponseError):
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


async def remove_vless_user(uuid: str) -> None:
    """
    Удалить пользователя VLESS из Xray Core.
    
    Вызывает POST /remove-user на Xray API сервере для удаления пользователя.
    
    В STAGE окружении UUID должен иметь префикс "stage-" для изоляции.
    При удалении префикс удаляется перед отправкой в XRAY API.
    
    Args:
        uuid: UUID пользователя для удаления (str, может быть с префиксом "stage-" в STAGE)
    
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
    
    if not uuid or not uuid.strip():
        error_msg = f"Invalid UUID provided: {uuid}"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    # STAGE изоляция: удаляем префикс перед отправкой в XRAY API
    uuid_clean = uuid.strip()
    original_uuid = uuid_clean
    if config.IS_STAGE and uuid_clean.startswith("stage-"):
        uuid_clean = uuid_clean[6:]  # Удаляем "stage-" префикс
        logger.info(f"XRAY_CALL_STAGE_ISOLATION [prefixed_uuid={original_uuid[:14]}..., original_uuid={uuid_clean[:8]}...]")
    
    # STAGE изоляция: логируем начало операции
    if config.IS_STAGE:
        logger.info(f"XRAY_CALL_START [operation=remove_user, uuid={original_uuid[:14]}..., environment=stage]")
    elif config.IS_PROD:
        logger.info(f"XRAY_CALL_START [operation=remove_user, uuid={uuid_clean[:8]}..., environment=prod]")
    else:
        logger.info(f"XRAY_CALL_START [operation=remove_user, uuid={uuid_clean[:8]}..., environment=local]")
    
    # Проверяем что URL правильный и не является private IP
    api_url = config.XRAY_API_URL.rstrip('/')
    if not api_url.startswith('http://') and not api_url.startswith('https://'):
        error_msg = f"Invalid XRAY_API_URL format: {api_url}. Must start with http:// or https://"
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
                f"Expected format: https://api.myvpncloud.net"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)
    
    # Используем формат /remove-user/{uuid} (UUID в пути, не в body)
    # uuid_clean уже обработан выше (префикс удален в STAGE)
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
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
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
            
        except (AuthError, ValueError):
            # Domain exceptions should NOT be retried - raise immediately
            logger.error(f"XRAY_CALL_FAILED [operation=remove_user, error_type=domain_error, uuid={uuid_preview}, environment={config.APP_ENV}, error={str(e)[:100]}]")
            raise
        except Exception as e:
            # All other exceptions are wrapped by retry_async or are unexpected
            error_msg = f"Failed to remove VLESS user: {e}"
            logger.error(f"XRAY_CALL_FAILED [operation=remove_user, error_type=transient_error, uuid={uuid_preview}, environment={config.APP_ENV}, error={error_msg[:100]}]")
            raise VPNAPIError(error_msg) from e


async def reissue_vpn_access(old_uuid: str) -> str:
    """
    Перевыпустить VPN доступ: удалить старый UUID и создать новый.
    
    КРИТИЧЕСКИ ВАЖНО:
    - Если add-user упал → НЕ удалять старый UUID (он уже удалён)
    - Если remove-user упал → прервать операцию, старый UUID остаётся
    - Все шаги логируются
    
    Args:
        old_uuid: Старый UUID для удаления
    
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
    
    # ШАГ 2: Создаём новый UUID
    try:
        vless_result = await add_vless_user()
        new_uuid = vless_result.get("uuid")
        
        if not new_uuid:
            error_msg = "VPN API returned empty UUID during reissue"
            logger.error(f"VPN key reissue: ADD_FAILED [error={error_msg}]")
            raise VPNAPIError(error_msg)
        
        new_uuid_preview = f"{new_uuid[:8]}..." if new_uuid and len(new_uuid) > 8 else (new_uuid or "N/A")
        logger.info(f"VPN key reissue: SUCCESS [old_uuid={uuid_preview}, new_uuid={new_uuid_preview}]")
        
        return new_uuid
        
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
