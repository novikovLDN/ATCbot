"""
FastAPI сервер для управления пользователями Xray Core (VLESS + REALITY)

Сервер работает локально (127.0.0.1:8000) и управляет UUID в Xray config.json.
Защищён через API-ключ в заголовке X-API-Key.
"""
import os
import json
import uuid
import logging
import subprocess
import shutil
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Xray Core Management API",
    description="API для управления пользователями Xray Core (VLESS + REALITY)",
    version="1.0.0"
)

# ============================================================================
# Конфигурация из переменных окружения
# ============================================================================

XRAY_API_KEY = os.getenv("XRAY_API_KEY")
if not XRAY_API_KEY:
    raise ValueError("XRAY_API_KEY environment variable is required")

XRAY_CONFIG_PATH = os.getenv("XRAY_CONFIG_PATH", "/usr/local/etc/xray/config.json")
XRAY_SERVER_IP = os.getenv("XRAY_SERVER_IP", "172.86.67.9")
XRAY_PORT = int(os.getenv("XRAY_PORT", "443"))
XRAY_SNI = os.getenv("XRAY_SNI", "www.cloudflare.com")
XRAY_PUBLIC_KEY = os.getenv("XRAY_PUBLIC_KEY", "fDixPEehAKSEsRGm5Q9HY-BNs9uMmN5NIzEDKngDOk8")
XRAY_SHORT_ID = os.getenv("XRAY_SHORT_ID", "a1b2c3d4")
XRAY_FLOW = os.getenv("XRAY_FLOW", "xtls-rprx-vision")
XRAY_FP = os.getenv("XRAY_FP", "chrome")

logger.info(f"Xray API initialized: config_path={XRAY_CONFIG_PATH}, server_ip={XRAY_SERVER_IP}")


# ============================================================================
# Модели данных
# ============================================================================

class RemoveUserRequest(BaseModel):
    uuid: str = Field(..., description="UUID пользователя для удаления")


class AddUserResponse(BaseModel):
    uuid: str
    vless_link: str


class RemoveUserResponse(BaseModel):
    status: str


class HealthResponse(BaseModel):
    status: str


# ============================================================================
# Вспомогательные функции
# ============================================================================

def validate_uuid(uuid_str: str) -> bool:
    """Проверить валидность UUID"""
    try:
        uuid.UUID(uuid_str)
        return True
    except (ValueError, TypeError):
        return False


def generate_vless_link(uuid_str: str) -> str:
    """
    Генерирует VLESS ссылку для подключения к Xray серверу.
    
    Формат:
    vless://UUID@SERVER_IP:PORT?encryption=none&flow=xtls-rprx-vision&security=reality&sni=SNI&fp=FP&pbk=PUBLIC_KEY&sid=SHORT_ID&type=tcp#VPN
    """
    server_address = f"{uuid_str}@{XRAY_SERVER_IP}:{XRAY_PORT}"
    
    params = {
        "encryption": "none",
        "flow": XRAY_FLOW,
        "security": "reality",
        "sni": XRAY_SNI,
        "fp": XRAY_FP,
        "pbk": XRAY_PUBLIC_KEY,
        "sid": XRAY_SHORT_ID,
        "type": "tcp"
    }
    
    query_parts = [f"{key}={quote(str(value))}" for key, value in params.items()]
    query_string = "&".join(query_parts)
    
    fragment = "VPN"
    vless_url = f"vless://{server_address}?{query_string}#{quote(fragment)}"
    
    return vless_url


def load_xray_config() -> dict:
    """Загрузить конфигурацию Xray из файла"""
    config_path = Path(XRAY_CONFIG_PATH)
    
    if not config_path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Xray config file not found: {XRAY_CONFIG_PATH}"
        )
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        return config
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Xray config JSON: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Invalid JSON in Xray config: {e}"
        )
    except Exception as e:
        logger.error(f"Failed to read Xray config: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read Xray config: {e}"
        )


def save_xray_config(config: dict) -> None:
    """
    Сохранить конфигурацию Xray в файл атомарно.
    
    Использует временный файл и переименование для атомарности записи.
    """
    config_path = Path(XRAY_CONFIG_PATH)
    temp_path = config_path.with_suffix('.json.tmp')
    
    try:
        # Записываем во временный файл
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        
        # Атомарно переименовываем
        shutil.move(str(temp_path), str(config_path))
        
        logger.info(f"Xray config saved successfully: {XRAY_CONFIG_PATH}")
    except Exception as e:
        # Удаляем временный файл при ошибке
        if temp_path.exists():
            temp_path.unlink()
        logger.error(f"Failed to save Xray config: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save Xray config: {e}"
        )


def restart_xray() -> None:
    """Перезапустить Xray через systemctl"""
    try:
        result = subprocess.run(
            ["systemctl", "restart", "xray"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            logger.error(f"Failed to restart Xray: {result.stderr}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to restart Xray: {result.stderr}"
            )
        
        logger.info("Xray restarted successfully")
    except subprocess.TimeoutExpired:
        logger.error("Timeout while restarting Xray")
        raise HTTPException(
            status_code=500,
            detail="Timeout while restarting Xray"
        )
    except FileNotFoundError:
        logger.error("systemctl command not found")
        raise HTTPException(
            status_code=500,
            detail="systemctl command not found. Is this running on a systemd system?"
        )
    except Exception as e:
        logger.error(f"Error restarting Xray: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error restarting Xray: {e}"
        )


def find_client_in_config(config: dict, target_uuid: str) -> Optional[int]:
    """
    Найти индекс клиента с указанным UUID в конфигурации.
    
    Returns:
        Индекс клиента или None, если не найден
    """
    try:
        inbounds = config.get("inbounds", [])
        if not inbounds:
            return None
        
        # Ищем первый inbound с VLESS
        for inbound in inbounds:
            if inbound.get("protocol") != "vless":
                continue
            
            clients = inbound.get("settings", {}).get("clients", [])
            for idx, client in enumerate(clients):
                if client.get("id") == target_uuid:
                    return idx
        
        return None
    except Exception as e:
        logger.error(f"Error finding client in config: {e}")
        return None


# ============================================================================
# Middleware для проверки API-ключа
# ============================================================================

@app.middleware("http")
async def verify_api_key(request: Request, call_next):
    """Проверка API-ключа для всех запросов кроме /health"""
    if request.url.path == "/health":
        return await call_next(request)
    
    api_key = request.headers.get("X-API-Key")
    if not api_key or api_key != XRAY_API_KEY:
        logger.warning(f"Unauthorized request from {request.client.host}: invalid API key")
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key"}
        )
    
    return await call_next(request)


# ============================================================================
# Эндпоинты
# ============================================================================

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Проверка здоровья сервера"""
    return HealthResponse(status="ok")


@app.post("/add-user", response_model=AddUserResponse)
async def add_user():
    """
    Добавить нового пользователя в Xray.
    
    Генерирует UUID, добавляет клиента в config.json и перезапускает Xray.
    """
    try:
        # Генерируем новый UUID
        new_uuid = str(uuid.uuid4())
        logger.info(f"Generating new UUID: {new_uuid}")
        
        # Загружаем конфигурацию
        config = load_xray_config()
        
        # Находим первый VLESS inbound
        inbounds = config.get("inbounds", [])
        vless_inbound = None
        
        for inbound in inbounds:
            if inbound.get("protocol") == "vless":
                vless_inbound = inbound
                break
        
        if not vless_inbound:
            raise HTTPException(
                status_code=500,
                detail="VLESS inbound not found in Xray config"
            )
        
        # Получаем список клиентов
        if "settings" not in vless_inbound:
            vless_inbound["settings"] = {}
        settings = vless_inbound["settings"]
        
        if "clients" not in settings:
            settings["clients"] = []
        clients = settings["clients"]
        
        # Проверяем, что UUID ещё не существует
        existing_uuids = [client.get("id") for client in clients if client.get("id")]
        if new_uuid in existing_uuids:
            logger.warning(f"UUID {new_uuid} already exists, generating new one")
            new_uuid = str(uuid.uuid4())
        
        # Добавляем нового клиента
        new_client = {
            "id": new_uuid,
            "flow": XRAY_FLOW
        }
        clients.append(new_client)
        
        logger.info(f"Adding client to config: uuid={new_uuid}")
        
        # Сохраняем конфигурацию
        save_xray_config(config)
        
        # Перезапускаем Xray
        restart_xray()
        
        # Генерируем VLESS ссылку
        vless_link = generate_vless_link(new_uuid)
        
        logger.info(f"User added successfully: uuid={new_uuid}")
        
        return AddUserResponse(
            uuid=new_uuid,
            vless_link=vless_link
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error adding user: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@app.post("/remove-user", response_model=RemoveUserResponse)
async def remove_user(request: RemoveUserRequest):
    """
    Удалить пользователя из Xray.
    
    Удаляет UUID из config.json и перезапускает Xray.
    """
    try:
        target_uuid = request.uuid.strip()
        
        # Валидация UUID
        if not validate_uuid(target_uuid):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid UUID format: {target_uuid}"
            )
        
        logger.info(f"Removing user: uuid={target_uuid}")
        
        # Загружаем конфигурацию
        config = load_xray_config()
        
        # Находим клиента в конфигурации
        inbounds = config.get("inbounds", [])
        client_found = False
        
        for inbound in inbounds:
            if inbound.get("protocol") != "vless":
                continue
            
            clients = inbound.get("settings", {}).get("clients", [])
            
            # Удаляем клиента с указанным UUID
            original_count = len(clients)
            clients[:] = [client for client in clients if client.get("id") != target_uuid]
            
            if len(clients) < original_count:
                client_found = True
                logger.info(f"Client removed from inbound: uuid={target_uuid}")
                break
        
        if not client_found:
            logger.warning(f"Client not found in config: uuid={target_uuid}")
            # Возвращаем успех даже если клиент не найден (идемпотентность)
            return RemoveUserResponse(status="ok")
        
        # Сохраняем конфигурацию
        save_xray_config(config)
        
        # Перезапускаем Xray
        restart_xray()
        
        logger.info(f"User removed successfully: uuid={target_uuid}")
        
        return RemoveUserResponse(status="ok")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error removing user: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


# ============================================================================
# Обработка ошибок
# ============================================================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Глобальный обработчик исключений"""
    logger.exception(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        log_level="info"
    )

