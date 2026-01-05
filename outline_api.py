"""
Модуль для работы с Outline Management API
"""
import httpx
import logging
from typing import Optional, Dict, Any, Tuple
import config

logger = logging.getLogger(__name__)


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
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(f"{config.OUTLINE_API_URL}/access-keys")
            
            if response.status_code == 201:
                data = response.json()
                key_id = data.get("id")
                access_url = data.get("accessUrl")
                
                if key_id is not None and access_url:
                    # Приводим key_id к int, так как БД ожидает INTEGER
                    key_id_int = int(key_id)
                    logger.info(f"Outline key created: key_id={key_id_int}")
                    return (key_id_int, access_url)
                else:
                    logger.error(f"Invalid response from Outline API: missing id or accessUrl")
                    return None
            else:
                error_text = response.text
                logger.error(f"Outline API error creating key: status={response.status_code}, error={error_text}")
                return None
    except httpx.HTTPError as e:
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
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.delete(f"{config.OUTLINE_API_URL}/access-keys/{key_id}")
            
            if response.status_code == 204:
                logger.info(f"Outline key deleted: key_id={key_id}")
                return True
            elif response.status_code == 404:
                # Ключ уже удален или не существует - не критичная ошибка
                logger.warning(f"Outline key not found (already deleted?): key_id={key_id}")
                return True  # Считаем успехом, так как цель достигнута
            else:
                error_text = response.text
                logger.error(f"Outline API error deleting key: key_id={key_id}, status={response.status_code}, error={error_text}")
                return False
    except httpx.HTTPError as e:
        logger.error(f"Network error deleting Outline key {key_id}: {e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"Unexpected error deleting Outline key {key_id}: {e}", exc_info=True)
        return False


async def get_outline_key_traffic(key_id: int) -> Optional[int]:
    """
    Получить количество переданных байт (bytesTransferred) для ключа доступа
    
    Args:
        key_id: ID ключа в Outline
    
    Returns:
        Количество байт (bytesTransferred) или None при ошибке
    """
    if not config.OUTLINE_API_URL:
        logger.error("OUTLINE_API_URL not set")
        return None
    
    if key_id is None:
        logger.warning("Attempt to get traffic for Outline key with None key_id")
        return None
    
    try:
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.get(f"{config.OUTLINE_API_URL}/access-keys/{key_id}")
            
            if response.status_code == 200:
                data = response.json()
                bytes_transferred = data.get("bytesTransferred", 0)
                # bytesTransferred может быть строкой или числом
                if isinstance(bytes_transferred, str):
                    try:
                        bytes_transferred = int(bytes_transferred)
                    except ValueError:
                        logger.warning(f"Invalid bytesTransferred format for key {key_id}: {bytes_transferred}")
                        return 0
                return int(bytes_transferred) if bytes_transferred is not None else 0
            elif response.status_code == 404:
                logger.warning(f"Outline key not found: key_id={key_id}")
                return None
            else:
                error_text = response.text
                logger.error(f"Outline API error getting traffic: key_id={key_id}, status={response.status_code}, error={error_text}")
                return None
    except httpx.HTTPError as e:
        logger.error(f"Network error getting Outline key traffic {key_id}: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Unexpected error getting Outline key traffic {key_id}: {e}", exc_info=True)
        return None

