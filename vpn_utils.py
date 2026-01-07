"""
Модуль для работы с Xray Core VPN API (VLESS + REALITY).

Этот модуль является единой точкой абстракции для работы с VPN инфраструктурой.
Все VPN операции должны выполняться через функции этого модуля.
"""
import httpx
import logging
from typing import Dict, Optional
import config

logger = logging.getLogger(__name__)

# HTTP клиент с таймаутами для API запросов
HTTP_TIMEOUT = 30.0  # секунды


async def add_vless_user() -> Dict[str, str]:
    """
    Создать нового пользователя VLESS в Xray Core.
    
    Вызывает POST /add-user на Xray API сервере и возвращает данные пользователя.
    
    Returns:
        Словарь с ключами:
        - "uuid": UUID пользователя (str)
        - "vless_url": VLESS URL для подключения (str)
    
    Raises:
        ValueError: Если XRAY_API_URL или XRAY_API_KEY не настроены
        httpx.HTTPError: При ошибках сети
        httpx.HTTPStatusError: При ошибках HTTP (4xx, 5xx)
        Exception: При других ошибках
    """
    if not config.XRAY_API_URL:
        error_msg = "XRAY_API_URL environment variable is not set"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    if not config.XRAY_API_KEY:
        error_msg = "XRAY_API_KEY environment variable is not set"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    url = f"{config.XRAY_API_URL.rstrip('/')}/add-user"
    headers = {
        "Authorization": f"Bearer {config.XRAY_API_KEY}",
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            logger.debug(f"Creating new VLESS user via {url}")
            response = await client.post(url, headers=headers)
            
            # Проверяем статус ответа
            response.raise_for_status()
            
            # Парсим JSON ответ
            data = response.json()
            
            # Валидируем структуру ответа
            uuid = data.get("uuid")
            vless_url = data.get("vless_url")
            
            if not uuid or not vless_url:
                error_msg = f"Invalid response from Xray API: missing 'uuid' or 'vless_url'. Response: {data}"
                logger.error(error_msg)
                raise ValueError(error_msg)
            
            logger.info(f"VLESS user created successfully: uuid={uuid}")
            
            return {
                "uuid": str(uuid),
                "vless_url": str(vless_url)
            }
            
    except httpx.TimeoutException as e:
        error_msg = f"Timeout while creating VLESS user: {e}"
        logger.error(error_msg)
        raise httpx.HTTPError(error_msg) from e
        
    except httpx.HTTPStatusError as e:
        error_msg = (
            f"Xray API error creating user: "
            f"status={e.response.status_code}, "
            f"response={e.response.text}"
        )
        logger.error(error_msg)
        raise
        
    except httpx.HTTPError as e:
        error_msg = f"Network error creating VLESS user: {e}"
        logger.error(error_msg, exc_info=True)
        raise
        
    except ValueError:
        # Re-raise ValueError (validation errors)
        raise
        
    except Exception as e:
        error_msg = f"Unexpected error creating VLESS user: {e}"
        logger.error(error_msg, exc_info=True)
        raise Exception(error_msg) from e


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
    
    url = f"{config.XRAY_API_URL.rstrip('/')}/remove-user"
    headers = {
        "Authorization": f"Bearer {config.XRAY_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "uuid": uuid.strip()
    }
    
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            logger.debug(f"Removing VLESS user uuid={uuid} via {url}")
            response = await client.post(url, headers=headers, json=payload)
            
            # Проверяем статус ответа
            response.raise_for_status()
            
            logger.info(f"VLESS user removed successfully: uuid={uuid}")
            
    except httpx.TimeoutException as e:
        error_msg = f"Timeout while removing VLESS user uuid={uuid}: {e}"
        logger.error(error_msg)
        raise httpx.HTTPError(error_msg) from e
        
    except httpx.HTTPStatusError as e:
        error_msg = (
            f"Xray API error removing user uuid={uuid}: "
            f"status={e.response.status_code}, "
            f"response={e.response.text}"
        )
        logger.error(error_msg)
        raise
        
    except httpx.HTTPError as e:
        error_msg = f"Network error removing VLESS user uuid={uuid}: {e}"
        logger.error(error_msg, exc_info=True)
        raise
        
    except ValueError:
        # Re-raise ValueError (validation errors)
        raise
        
    except Exception as e:
        error_msg = f"Unexpected error removing VLESS user uuid={uuid}: {e}"
        logger.error(error_msg, exc_info=True)
        raise Exception(error_msg) from e


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
