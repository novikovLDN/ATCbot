"""
Модуль для работы с Xray Core VPN API (VLESS + REALITY).

Этот модуль является единой точкой абстракции для работы с VPN инфраструктурой.
Все VPN операции должны выполняться через функции этого модуля.
"""
import httpx
import logging
import asyncio
from typing import Dict, Optional
from urllib.parse import quote
import config

logger = logging.getLogger(__name__)

# HTTP клиент с таймаутами для API запросов
HTTP_TIMEOUT = 10.0  # секунды (≥ 10 секунд по требованию)
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


def generate_vless_url(uuid: str) -> str:
    """
    Генерирует VLESS URL для подключения к Xray Core серверу.
    
    Формат:
    vless://UUID@SERVER_IP:PORT
    ?encryption=none
    &flow=xtls-rprx-vision
    &security=reality
    &sni=www.cloudflare.com
    &fp=chrome
    &pbk=PUBLIC_KEY
    &sid=SHORT_ID
    &type=tcp
    #AtlasSecure
    
    Args:
        uuid: UUID пользователя
    
    Returns:
        VLESS URL строка
    """
    # Кодируем параметры для URL
    server_address = f"{uuid}@{config.XRAY_SERVER_IP}:{config.XRAY_PORT}"
    
    # Параметры запроса
    params = {
        "encryption": "none",
        "flow": config.XRAY_FLOW,
        "security": "reality",
        "sni": config.XRAY_SNI,
        "fp": config.XRAY_FP,
        "pbk": config.XRAY_PUBLIC_KEY,
        "sid": config.XRAY_SHORT_ID,
        "type": "tcp"
    }
    
    # Формируем query string
    query_parts = [f"{key}={quote(str(value))}" for key, value in params.items()]
    query_string = "&".join(query_parts)
    
    # Формируем полный URL
    fragment = "AtlasSecure"
    vless_url = f"vless://{server_address}?{query_string}#{quote(fragment)}"
    
    return vless_url


async def add_vless_user() -> Dict[str, str]:
    """
    Создать нового пользователя VLESS в Xray Core.
    
    Вызывает POST /add-user на локальном FastAPI VPN API сервере.
    API возвращает только UUID, а VLESS URL генерируется локально.
    
    Returns:
        Словарь с ключами:
        - "uuid": UUID пользователя (str)
        - "vless_url": VLESS URL для подключения (str, сгенерирован локально)
    
    Raises:
        ValueError: Если XRAY_API_URL или XRAY_API_KEY не настроены
        httpx.HTTPError: При ошибках сети
        httpx.HTTPStatusError: При ошибках HTTP (4xx, 5xx)
        Exception: При других ошибках
    """
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
    
    # Проверяем что URL правильный (должен быть http://127.0.0.1:8000 или https://...)
    api_url = config.XRAY_API_URL.rstrip('/')
    if not api_url.startswith('http://') and not api_url.startswith('https://'):
        error_msg = f"Invalid XRAY_API_URL format: {api_url}. Must start with http:// or https://"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    url = f"{api_url}/add-user"
    headers = {
        "X-API-Key": config.XRAY_API_KEY,
        "Content-Type": "application/json"
    }
    
    # Логируем начало операции
    logger.info(f"vpn_api add_user: START [url={url}]")
    
    last_exception = None
    for attempt in range(MAX_RETRIES + 1):
        # Backoff: увеличиваем задержку с каждой попыткой
        if attempt > 0:
            delay = RETRY_DELAY * attempt
            logger.info(f"vpn_api add_user: RETRY [attempt={attempt + 1}/{MAX_RETRIES + 1}, delay={delay}s]")
            await asyncio.sleep(delay)
        
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                logger.debug(f"vpn_api add_user: ATTEMPT [attempt={attempt + 1}/{MAX_RETRIES + 1}]")
                response = await client.post(url, headers=headers)
                
                # Логируем статус ответа
                logger.info(f"vpn_api add_user: RESPONSE [status={response.status_code}, attempt={attempt + 1}]")
                
                # Проверяем статус ответа
                if response.status_code == 401 or response.status_code == 403:
                    error_msg = f"Authentication error: status={response.status_code}, response={response.text[:200]}"
                    logger.error(f"vpn_api add_user: AUTH_ERROR [{error_msg}]")
                    raise AuthError(error_msg)
                
                response.raise_for_status()
                
                # Парсим JSON ответ (API возвращает uuid и vless_link)
                try:
                    data = response.json()
                except Exception as e:
                    error_msg = f"Invalid JSON response: {response.text[:200]}"
                    logger.error(f"vpn_api add_user: INVALID_JSON [{error_msg}]")
                    raise InvalidResponseError(error_msg) from e
                
                # Валидируем структуру ответа
                uuid = data.get("uuid")
                vless_link = data.get("vless_link")
                
                if not uuid:
                    error_msg = f"Invalid response from Xray API: missing 'uuid'. Response: {data}"
                    logger.error(f"vpn_api add_user: INVALID_RESPONSE [{error_msg}]")
                    raise InvalidResponseError(error_msg)
                
                # Используем vless_link из ответа API, если есть, иначе генерируем локально
                if vless_link:
                    vless_url = vless_link
                else:
                    # Генерируем VLESS URL локально на основе UUID + серверных констант (fallback)
                    vless_url = generate_vless_url(str(uuid))
                
                # Безопасное логирование UUID (только первые 8 символов)
                uuid_preview = f"{uuid[:8]}..." if uuid and len(uuid) > 8 else (uuid or "N/A")
                logger.info(f"vpn_api add_user: SUCCESS [uuid={uuid_preview}, attempt={attempt + 1}]")
                
                return {
                    "uuid": str(uuid),
                    "vless_url": vless_url
                }
                
        except httpx.TimeoutException as e:
            last_exception = e
            error_msg = f"Timeout while creating VLESS user (attempt {attempt + 1}/{MAX_RETRIES + 1})"
            logger.error(f"vpn_api add_user: TIMEOUT [{error_msg}]")
            if attempt < MAX_RETRIES:
                continue
            raise TimeoutError(error_msg) from e
            
        except AuthError:
            # AuthError не retry
            raise
            
        except InvalidResponseError:
            # InvalidResponseError не retry
            raise
            
        except httpx.HTTPStatusError as e:
            last_exception = e
            error_msg = (
                f"HTTP error creating user (attempt {attempt + 1}/{MAX_RETRIES + 1}): "
                f"status={e.response.status_code}, "
                f"response_body={e.response.text[:200]}"
            )
            logger.error(f"vpn_api add_user: HTTP_ERROR [{error_msg}]")
            # Для HTTP ошибок не делаем retry (4xx/5xx не исправятся)
            raise VPNAPIError(error_msg) from e
            
        except httpx.HTTPError as e:
            last_exception = e
            error_msg = f"Network error creating VLESS user (attempt {attempt + 1}/{MAX_RETRIES + 1}): {e}"
            logger.error(f"vpn_api add_user: NETWORK_ERROR [{error_msg}]")
            if attempt < MAX_RETRIES:
                continue
            raise VPNAPIError(error_msg) from e
            
        except (ValueError, AuthError, InvalidResponseError, TimeoutError):
            # Re-raise специальные исключения - не retry
            raise
            
        except Exception as e:
            last_exception = e
            error_msg = f"Unexpected error creating VLESS user (attempt {attempt + 1}/{MAX_RETRIES + 1}): {e}"
            logger.error(f"vpn_api add_user: UNEXPECTED_ERROR [{error_msg}]", exc_info=True)
            if attempt < MAX_RETRIES:
                continue
            raise VPNAPIError(error_msg) from e
    
    # Если дошли сюда - все попытки исчерпаны
    if last_exception:
        if isinstance(last_exception, httpx.TimeoutException):
            raise TimeoutError(f"Timeout after {MAX_RETRIES + 1} attempts") from last_exception
        raise VPNAPIError(f"Failed after {MAX_RETRIES + 1} attempts: {last_exception}") from last_exception
    raise VPNAPIError("Failed to create VLESS user: all retries exhausted")


async def remove_vless_user(uuid: str) -> None:
    """
    Удалить пользователя VLESS из Xray Core.
    
    Вызывает POST /remove-user на Xray API сервере для удаления пользователя.
    
    Args:
        uuid: UUID пользователя для удаления (str)
    
    Raises:
        ValueError: Если XRAY_API_URL или XRAY_API_KEY не настроены, или uuid пустой
        httpx.HTTPError: При ошибках сети
        httpx.HTTPStatusError: При ошибках HTTP (4xx, 5xx)
        Exception: При других ошибках
    
    Note:
        Функция НЕ игнорирует ошибки. Если удаление не удалось,
        будет выброшено исключение.
    """
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
    
    # Проверяем что URL правильный
    api_url = config.XRAY_API_URL.rstrip('/')
    if not api_url.startswith('http://') and not api_url.startswith('https://'):
        error_msg = f"Invalid XRAY_API_URL format: {api_url}. Must start with http:// or https://"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    url = f"{api_url}/remove-user"
    headers = {
        "X-API-Key": config.XRAY_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "uuid": uuid.strip()
    }
    
    # Безопасное логирование UUID
    uuid_preview = f"{uuid[:8]}..." if uuid and len(uuid) > 8 else (uuid or "N/A")
    logger.info(f"vpn_api remove_user: START [uuid={uuid_preview}, url={url}]")
    
    last_exception = None
    for attempt in range(MAX_RETRIES + 1):
        # Backoff: увеличиваем задержку с каждой попыткой
        if attempt > 0:
            delay = RETRY_DELAY * attempt
            logger.info(f"vpn_api remove_user: RETRY [uuid={uuid_preview}, attempt={attempt + 1}/{MAX_RETRIES + 1}, delay={delay}s]")
            await asyncio.sleep(delay)
        
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                logger.debug(f"vpn_api remove_user: ATTEMPT [uuid={uuid_preview}, attempt={attempt + 1}/{MAX_RETRIES + 1}]")
                response = await client.post(url, headers=headers, json=payload)
                
                # Логируем статус ответа
                logger.info(f"vpn_api remove_user: RESPONSE [uuid={uuid_preview}, status={response.status_code}, attempt={attempt + 1}]")
                
                # Проверяем статус ответа
                if response.status_code == 401 or response.status_code == 403:
                    error_msg = f"Authentication error: status={response.status_code}, response={response.text[:200]}"
                    logger.error(f"vpn_api remove_user: AUTH_ERROR [uuid={uuid_preview}, {error_msg}]")
                    raise AuthError(error_msg)
                
                response.raise_for_status()
                
                logger.info(f"vpn_api remove_user: SUCCESS [uuid={uuid_preview}, attempt={attempt + 1}]")
                return
                
        except httpx.TimeoutException as e:
            last_exception = e
            error_msg = f"Timeout while removing VLESS user (attempt {attempt + 1}/{MAX_RETRIES + 1})"
            logger.error(f"vpn_api remove_user: TIMEOUT [uuid={uuid_preview}, {error_msg}]")
            if attempt < MAX_RETRIES:
                continue
            raise TimeoutError(error_msg) from e
            
        except AuthError:
            # AuthError не retry
            raise
            
        except httpx.HTTPStatusError as e:
            last_exception = e
            error_msg = (
                f"HTTP error removing user (attempt {attempt + 1}/{MAX_RETRIES + 1}): "
                f"status={e.response.status_code}, "
                f"response_body={e.response.text[:200]}"
            )
            logger.error(f"vpn_api remove_user: HTTP_ERROR [uuid={uuid_preview}, {error_msg}]")
            # Для HTTP ошибок не делаем retry (4xx/5xx не исправятся)
            raise VPNAPIError(error_msg) from e
            
        except httpx.HTTPError as e:
            last_exception = e
            error_msg = f"Network error removing VLESS user (attempt {attempt + 1}/{MAX_RETRIES + 1}): {e}"
            logger.error(f"vpn_api remove_user: NETWORK_ERROR [uuid={uuid_preview}, {error_msg}]")
            if attempt < MAX_RETRIES:
                continue
            raise VPNAPIError(error_msg) from e
            
        except (ValueError, AuthError, TimeoutError):
            # Re-raise специальные исключения - не retry
            raise
            
        except Exception as e:
            last_exception = e
            error_msg = f"Unexpected error removing VLESS user (attempt {attempt + 1}/{MAX_RETRIES + 1}): {e}"
            logger.error(f"vpn_api remove_user: UNEXPECTED_ERROR [uuid={uuid_preview}, {error_msg}]", exc_info=True)
            if attempt < MAX_RETRIES:
                continue
            raise VPNAPIError(error_msg) from e
    
    # Если дошли сюда - все попытки исчерпаны
    if last_exception:
        if isinstance(last_exception, httpx.TimeoutException):
            raise TimeoutError(f"Timeout after {MAX_RETRIES + 1} attempts") from last_exception
        raise VPNAPIError(f"Failed after {MAX_RETRIES + 1} attempts: {last_exception}") from last_exception
    raise VPNAPIError(f"Failed to remove VLESS user uuid={uuid_preview}: all retries exhausted")


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
