"""
Модуль для работы с Outline Management API
"""
import aiohttp
import logging
import ssl
from typing import Optional, Dict, Any, Tuple
import config

logger = logging.getLogger(__name__)

# SSL контекст с отключенной проверкой сертификата
# (Outline Manager использует self-signed сертификат)
_ssl_context = ssl.create_default_context()
_ssl_context.check_hostname = False
_ssl_context.verify_mode = ssl.CERT_NONE

# TCP Connector с отключенной проверкой SSL
_connector = aiohttp.TCPConnector(ssl=_ssl_context)


async def create_outline_key() -> Optional[Tuple[int, str]]:
    """
    Создать новый ключ доступа в Outline
    
    Returns:
        Кортеж (key_id, access_url) или None при ошибке
        key_id - ID ключа в Outline
        access_url - URL для подключения (ss://...)
    """
    if not config.OUTLINE_API_URL:
        logger.error("OUTLINE_API_URL not set")
        return None
    
    try:
        async with aiohttp.ClientSession(connector=_connector) as session:
            async with session.post(f"{config.OUTLINE_API_URL}/access-keys") as response:
                if response.status == 201:
                    data = await response.json()
                    key_id = data.get("id")
                    access_url = data.get("accessUrl")
                    
                    if key_id is not None and access_url:
                        logger.info(f"Outline key created: key_id={key_id}")
                        return (key_id, access_url)
                    else:
                        logger.error(f"Invalid response from Outline API: missing id or accessUrl")
                        return None
                else:
                    error_text = await response.text()
                    logger.error(f"Outline API error creating key: status={response.status}, error={error_text}")
                    return None
    except aiohttp.ClientError as e:
        logger.error(f"Network error creating Outline key: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Unexpected error creating Outline key: {e}", exc_info=True)
        return None


async def delete_outline_key(key_id: int) -> bool:
    """
    Удалить ключ доступа из Outline
    
    Args:
        key_id: ID ключа в Outline
    
    Returns:
        True если успешно, False при ошибке
    """
    if not config.OUTLINE_API_URL:
        logger.error("OUTLINE_API_URL not set")
        return False
    
    if key_id is None:
        logger.warning("Attempt to delete Outline key with None key_id")
        return False
    
    try:
        async with aiohttp.ClientSession(connector=_connector) as session:
            async with session.delete(f"{config.OUTLINE_API_URL}/access-keys/{key_id}") as response:
                if response.status == 204:
                    logger.info(f"Outline key deleted: key_id={key_id}")
                    return True
                elif response.status == 404:
                    # Ключ уже удален или не существует - не критичная ошибка
                    logger.warning(f"Outline key not found (already deleted?): key_id={key_id}")
                    return True  # Считаем успехом, так как цель достигнута
                else:
                    error_text = await response.text()
                    logger.error(f"Outline API error deleting key: key_id={key_id}, status={response.status}, error={error_text}")
                    return False
    except aiohttp.ClientError as e:
        logger.error(f"Network error deleting Outline key {key_id}: {e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"Unexpected error deleting Outline key {key_id}: {e}", exc_info=True)
        return False

