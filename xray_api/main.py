"""
FastAPI сервер для управления пользователями Xray Core (VLESS + REALITY)

Сервер работает локально (127.0.0.1:8000) и управляет UUID в Xray config.json.
Защищён через API-ключ в заголовке X-API-Key.

Production: All file I/O and subprocess calls run off the event loop via asyncio.to_thread
or asyncio.create_subprocess_exec to keep the API non-blocking.
"""
import asyncio
import os
import json
import time
import uuid
import logging
import shutil
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Use shared logging config (INFO/WARNING→stdout, ERROR→stderr)
from app.core.logging_config import setup_logging
setup_logging()
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
XRAY_SERVER_IP = os.getenv("XRAY_SERVER_IP", "vpn.mynewllcw.com")
XRAY_PORT = int(os.getenv("XRAY_PORT", "443"))
XRAY_SNI = os.getenv("XRAY_SNI", "vpn.mynewllcw.com")
XRAY_PUBLIC_KEY = os.getenv("XRAY_PUBLIC_KEY", "Aar4hQAtl1QEtaz3_euuXNuQpWpr_d3Yko4n4CXpI7Y")
XRAY_SHORT_ID = os.getenv("XRAY_SHORT_ID", "12345678")
# XRAY_FLOW удалён: flow ЗАПРЕЩЁН для REALITY — only in Xray config, not in link
XRAY_FP = os.getenv("XRAY_FP", "chrome")

logger.info(f"Xray API initialized: config_path={XRAY_CONFIG_PATH}, server_ip={XRAY_SERVER_IP}")

# Lock for config file write operations (prevents concurrent write races)
_config_file_lock = asyncio.Lock()


# Production safety invariant:
# XRAY_PORT must match inbound port in config.json to prevent generation of invalid VLESS links.
def _validate_xray_port_consistency() -> None:
    """Fail fast at startup if XRAY_PORT does not match config.json inbound port."""
    path = Path(XRAY_CONFIG_PATH)
    if not path.exists():
        logger.warning(f"Xray config not found at {XRAY_CONFIG_PATH}, skipping port validation")
        return
    try:
        with open(path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Could not load Xray config for port validation: {e}")
        return
    inbounds = cfg.get("inbounds", [])
    inbound_port = None
    for inbound in inbounds:
        if inbound.get("protocol") == "vless":
            inbound_port = inbound.get("port")
            break
    if inbound_port is None:
        logger.warning("No VLESS inbound found in config, skipping port validation")
        return
    if int(inbound_port) != XRAY_PORT:
        logger.critical(
            "XRAY_PORT_MISMATCH",
            extra={"env_port": XRAY_PORT, "config_port": inbound_port}
        )
        raise RuntimeError(
            f"XRAY_PORT ({XRAY_PORT}) does not match inbound port in config.json ({inbound_port})"
        )


# ============================================================================
# XrayMutationQueue — Deterministic batching, max 1 restart per 3 seconds
# ============================================================================

class XrayMutationQueue:
    """
    Queue-based mutation model. Config writes happen immediately (atomic).
    Restart is batched: max 1 restart per FLUSHER_INTERVAL.
    Survives burst traffic (100+ ops/min) without restart storm.
    """
    FLUSHER_INTERVAL = 3.0  # seconds
    
    def __init__(self):
        self._restart_pending = False
        self._lock = asyncio.Lock()
        self._mutation_count = 0
        self._flusher_task: Optional[asyncio.Task] = None
        self._stopped = False
    
    def mark_restart_pending(self, op: str = "mutation") -> None:
        """Mark that a restart is needed after config mutation."""
        self._restart_pending = True
        self._mutation_count += 1
        logger.debug(f"MutationQueue: restart_pending=True, op={op}, total_mutations={self._mutation_count}")
    
    def has_pending(self) -> bool:
        return self._restart_pending and not self._stopped
    
    async def flush(self) -> bool:
        """
        If restart pending: do single restart, clear pending.
        Returns True if restart was performed, False otherwise.
        """
        async with self._lock:
            if not self._restart_pending or self._stopped:
                return False
            self._restart_pending = False
            count = self._mutation_count
            self._mutation_count = 0
        
        # Restart outside lock to avoid blocking
        try:
            await _restart_xray_async()
            logger.info(f"MutationQueue: flush OK, restarted after {count} mutation(s)")
            return True
        except Exception as e:
            logger.warning(f"MutationQueue: restart failed, will retry: {e}")
            # Retry once
            try:
                await asyncio.sleep(1.0)
                await _restart_xray_async()
                logger.info(f"MutationQueue: flush OK on retry, restarted after {count} mutation(s)")
                return True
            except Exception as retry_e:
                logger.critical(
                    f"MutationQueue: RESTART_FAILED_AFTER_RETRY [mutations={count}, error={retry_e}] - "
                    "Config on disk is correct but Xray has not reloaded. Admin intervention required."
                )
                self._stopped = True
                # Re-mark pending so we keep trying on next cycle
                self._restart_pending = True
                self._mutation_count = count
                return False
    
    def start_flusher(self) -> None:
        """Start background flusher task."""
        if self._flusher_task and not self._flusher_task.done():
            return
        self._stopped = False
        self._flusher_task = asyncio.create_task(_flusher_loop(self))
        logger.info(f"MutationQueue: flusher started, interval={self.FLUSHER_INTERVAL}s")
    
    def stop_flusher(self) -> None:
        """Stop background flusher task."""
        if self._flusher_task:
            self._flusher_task.cancel()
            self._flusher_task = None
        logger.info("MutationQueue: flusher stopped")


_mutation_queue = XrayMutationQueue()


async def _flusher_loop(queue: XrayMutationQueue) -> None:
    """Background task: every FLUSHER_INTERVAL, flush pending restart."""
    while True:
        try:
            await asyncio.sleep(queue.FLUSHER_INTERVAL)
            if queue.has_pending():
                await queue.flush()
        except asyncio.CancelledError:
            logger.info("MutationQueue: flusher cancelled")
            break
        except Exception as e:
            logger.exception(f"MutationQueue: flusher error: {e}")


# ============================================================================
# Модели данных
# ============================================================================

class AddUserRequest(BaseModel):
    """UUID required. DB is source of truth. Xray never generates UUID."""
    uuid: str
    telegram_id: int
    expiry_timestamp_ms: int
    tariff: str = "basic"


class UpdateUserRequest(BaseModel):
    uuid: str
    expiry_timestamp_ms: int


class AddUserResponse(BaseModel):
    uuid: str
    vless_link: str
    link: str = ""  # Same as vless_link; both returned for API contract compatibility


class RemoveUserResponse(BaseModel):
    status: str


class UpdateUserResponse(BaseModel):
    status: str


class HealthResponse(BaseModel):
    status: str


class ListUsersResponse(BaseModel):
    """UUIDs of all VLESS clients in Xray config."""
    uuids: list[str]


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
    
    REALITY + XTLS Vision: flow=xtls-rprx-vision REQUIRED.
    Без flow трафик подключается, но не проходит.
    
    Формат:
    vless://UUID@SERVER_IP:PORT?
    encryption=none
    &security=reality
    &flow=xtls-rprx-vision
    &type=tcp
    &sni={REALITY_SNI}
    &fp=...
    &pbk={REALITY_PBK}
    &sid={REALITY_SID}
    #AtlasSecure
    """
    server_address = f"{uuid_str}@{XRAY_SERVER_IP}:{XRAY_PORT}"
    
    # REALITY + XTLS Vision: flow required for traffic to pass
    params = {
        "encryption": "none",
        "security": "reality",
        "flow": "xtls-rprx-vision",
        "type": "tcp",
        "sni": XRAY_SNI,
        "fp": XRAY_FP,
        "pbk": XRAY_PUBLIC_KEY,
        "sid": XRAY_SHORT_ID
    }
    
    query_parts = [f"{key}={quote(str(value))}" for key, value in params.items()]
    query_string = "&".join(query_parts)
    
    fragment = "AtlasSecure"
    vless_url = f"vless://{server_address}?{query_string}#{quote(fragment)}"
    
    return vless_url


def _load_xray_config_file(config_path: str) -> dict:
    """
    Sync helper: load Xray config from file.
    Runs off event loop via asyncio.to_thread.
    """
    path = Path(config_path)
    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Xray config file not found: {config_path}"
        )
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
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


def _save_xray_config_file(config: dict, config_path: str) -> None:
    """
    Sync helper: save Xray config to file atomically.
    Runs off event loop via asyncio.to_thread.
    Uses temp file + rename for atomic write.
    """
    path = Path(config_path)
    temp_path = path.with_suffix('.json.tmp')
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        shutil.move(str(temp_path), str(path))
        logger.info(f"Xray config saved successfully: {config_path}")
    except Exception as e:
        if temp_path.exists():
            temp_path.unlink()
        logger.error(f"Failed to save Xray config: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save Xray config: {e}"
        )


_last_restart_time: float = 0.0  # Last successful restart timestamp (for metrics)


async def _restart_xray_async() -> None:
    """Перезапустить Xray через systemctl (non-blocking via asyncio subprocess)"""
    global _last_restart_time
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "restart", "xray",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.error("Timeout while restarting Xray")
            raise HTTPException(
                status_code=500,
                detail="Timeout while restarting Xray"
            ) from None

        stderr = stderr_bytes.decode() if stderr_bytes else ""
        if proc.returncode != 0:
            logger.error(f"Failed to restart Xray: {stderr}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to restart Xray: {stderr}"
            )

        logger.info("Xray restarted successfully")
        _last_restart_time = time.time()
    except FileNotFoundError:
        logger.error("systemctl command not found")
        raise HTTPException(
            status_code=500,
            detail="systemctl command not found. Is this running on a systemd system?"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error restarting Xray: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error restarting Xray: {e}"
        )


def _mark_restart_pending(op: str = "mutation") -> None:
    """Mark restart pending; flusher will apply batched restart every 3s."""
    _mutation_queue.mark_restart_pending(op=op)


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


def _get_inbound_group(tag: str) -> str:
    """Determine inbound group from tag. Returns 'basic' or 'plus'."""
    if tag.startswith("plus"):
        return "plus"
    return "basic"


def _get_inbound_transport(inbound: dict) -> str:
    """Get transport type from inbound config."""
    return inbound.get("streamSettings", {}).get("network", "tcp")


def _get_inbounds_by_group(config: dict, group: str) -> list[dict]:
    """Get all VLESS inbounds belonging to a group ('basic' or 'plus')."""
    result = []
    for inbound in config.get("inbounds", []):
        if inbound.get("protocol") != "vless":
            continue
        tag = inbound.get("tag", "")
        if _get_inbound_group(tag) == group:
            result.append(inbound)
    return result


def _build_client_entry(uuid_str: str, email: str, expiry_ms: int, inbound: dict) -> dict:
    """Build a client entry dict appropriate for the inbound's transport type."""
    entry = {
        "id": uuid_str,
        "email": email,
        "expiryTime": expiry_ms,
    }
    # TCP requires flow; XHTTP must NOT have flow
    transport = _get_inbound_transport(inbound)
    if transport == "tcp":
        entry["flow"] = "xtls-rprx-vision"
    return entry


def _add_client_to_inbounds(config: dict, inbounds: list[dict], uuid_str: str, email: str, expiry_ms: int) -> int:
    """
    Add client to all specified inbounds. Idempotent: updates expiry if exists.
    Returns number of inbounds modified.
    """
    modified = 0
    for inbound in inbounds:
        if "settings" not in inbound:
            inbound["settings"] = {}
        if "clients" not in inbound["settings"]:
            inbound["settings"]["clients"] = []
        
        clients = inbound["settings"]["clients"]
        existing = None
        for c in clients:
            if c.get("id") == uuid_str:
                existing = c
                break
        
        if existing:
            existing["expiryTime"] = expiry_ms
            if "email" not in existing:
                existing["email"] = email
            # Fix flow: ensure correct based on transport
            transport = _get_inbound_transport(inbound)
            if transport == "tcp":
                existing["flow"] = "xtls-rprx-vision"
            elif "flow" in existing:
                del existing["flow"]  # Remove flow from xhttp
        else:
            new_client = _build_client_entry(uuid_str, email, expiry_ms, inbound)
            clients.append(new_client)
        modified += 1
    
    return modified


def _remove_client_from_inbounds(inbounds: list[dict], uuid_str: str) -> int:
    """Remove client from all specified inbounds. Returns number of inbounds modified."""
    modified = 0
    for inbound in inbounds:
        clients = inbound.get("settings", {}).get("clients", [])
        original_count = len(clients)
        clients[:] = [c for c in clients if c.get("id") != uuid_str]
        if len(clients) < original_count:
            modified += 1
    return modified


# ============================================================================
# Lifecycle: start/stop flusher
# ============================================================================

@app.on_event("startup")
async def startup_event():
    """Validate XRAY_PORT vs config, then start mutation queue flusher."""
    _validate_xray_port_consistency()
    _mutation_queue.start_flusher()


@app.on_event("shutdown")
async def shutdown_event():
    """Stop mutation queue flusher."""
    _mutation_queue.stop_flusher()


# ============================================================================
# Middleware для проверки API-ключа
# ============================================================================

@app.middleware("http")
async def verify_api_key(request: Request, call_next):
    """Проверка API-ключа для всех запросов кроме /health и /self-test"""
    if request.url.path in ("/health", "/self-test"):
        return await call_next(request)
    
    import hmac
    api_key = request.headers.get("X-API-Key")
    if not api_key or not hmac.compare_digest(api_key, XRAY_API_KEY):
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
    try:
        return HealthResponse(status="ok")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception("XRAY_API_ERROR")
        raise HTTPException(status_code=500, detail="internal_error")


@app.get("/self-test")
async def self_test(request: Request):
    """
    UUID contract verification: add test user, verify returned UUID matches sent, remove.
    Returns 200 {"status":"ok"} or 500 {"status":"uuid_mismatch"}.
    """
    import httpx
    test_uuid = str(uuid.uuid4())
    base_url = str(request.base_url).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{base_url}/add-user",
                headers={"X-API-Key": XRAY_API_KEY, "Content-Type": "application/json"},
                json={
                    "uuid": test_uuid,
                    "telegram_id": 0,
                    "expiry_timestamp_ms": 9999999999999,
                },
            )
            if r.status_code != 200:
                logger.error(f"SELF_TEST add-user failed: status={r.status_code} body={r.text[:200]}")
                return JSONResponse(status_code=500, content={"status": "add_user_failed", "detail": r.text[:100]})
            data = r.json()
            returned = data.get("uuid", "")
            if returned != test_uuid:
                logger.critical(f"SELF_TEST uuid_mismatch sent={test_uuid} returned={returned}")
                await client.post(
                    f"{base_url}/remove-user/{returned}",
                    headers={"X-API-Key": XRAY_API_KEY},
                )
                return JSONResponse(status_code=500, content={"status": "uuid_mismatch"})
            await client.post(
                f"{base_url}/remove-user/{test_uuid}",
                headers={"X-API-Key": XRAY_API_KEY},
            )
        return JSONResponse(status_code=200, content={"status": "ok"})
    except Exception as e:
        logger.exception(f"SELF_TEST error: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)[:100]})


@app.post("/add-user", response_model=AddUserResponse)
async def add_user(request: AddUserRequest):
    """
    Добавить нового пользователя в Xray.
    DB is source of truth: UUID required from request. Xray never generates UUID.
    Idempotent: if client exists, update expiry and return success.
    """
    try:
        uuid_from_request = str(request.uuid).strip()
        if not uuid_from_request:
            raise HTTPException(status_code=400, detail="uuid is required")
        if not validate_uuid(uuid_from_request):
            raise HTTPException(status_code=400, detail=f"Invalid UUID format: {uuid_from_request[:36]}")
        
        client_uuid = uuid_from_request
        logger.info(f"XRAY_ADD_CONTRACT uuid_request={uuid_from_request}")
        
        # Determine tariff from request (default basic)
        tariff = getattr(request, "tariff", "basic") or "basic"
        tariff = tariff.strip().lower()
        if tariff not in ("basic", "plus"):
            tariff = "basic"
        
        email = f"user_{request.telegram_id}"
        expiry_ms = request.expiry_timestamp_ms
        
        # Atomic read-modify-write: lock covers load + modify + save (fixes race under concurrent add-user)
        async with _config_file_lock:
            config = await asyncio.to_thread(_load_xray_config_file, XRAY_CONFIG_PATH)
            
            # Get inbounds for this tariff group
            # basic tariff → basic inbounds only
            # plus tariff → basic + plus inbounds
            groups_to_add = ["basic"]
            if tariff == "plus":
                groups_to_add.append("plus")
            
            total_modified = 0
            for group in groups_to_add:
                group_inbounds = _get_inbounds_by_group(config, group)
                modified = _add_client_to_inbounds(config, group_inbounds, client_uuid, email, expiry_ms)
                total_modified += modified
                logger.info(f"ADD_USER group={group} inbounds_modified={modified}")
            
            if total_modified == 0:
                raise HTTPException(
                    status_code=500,
                    detail="No VLESS inbounds found in Xray config"
                )
            
            logger.info(f"Adding client to {total_modified} inbounds: uuid={client_uuid[:8]}...")
            await asyncio.to_thread(_save_xray_config_file, config, XRAY_CONFIG_PATH)
        
        _mark_restart_pending("add_user")
        vless_link = generate_vless_link(client_uuid)
        if client_uuid not in vless_link:
            raise HTTPException(
                status_code=500,
                detail="UUID mismatch between request and generated link"
            )
        logger.info(
            f"ADD_USER_CONTRACT uuid_request={request.uuid} "
            f"uuid_response={client_uuid}"
        )
        return AddUserResponse(uuid=client_uuid, vless_link=vless_link, link=vless_link)

    except asyncio.CancelledError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("XRAY_API_ERROR")
        raise HTTPException(status_code=500, detail="internal_error")


@app.post("/remove-user/{uuid}", response_model=RemoveUserResponse)
async def remove_user(uuid: str):
    """
    Удалить пользователя из Xray.
    
    UUID передается в пути URL.
    Удаляет UUID из config.json и перезапускает Xray.
    Идемпотентно: если UUID не найден, возвращает успех.
    """
    try:
        target_uuid = uuid.strip()
        
        # Валидация UUID
        if not validate_uuid(target_uuid):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid UUID format: {target_uuid}"
            )
        
        logger.info(f"Removing user: uuid={target_uuid}")
        
        # Atomic read-modify-write: lock covers load + modify + save (fixes race under concurrent add/remove)
        async with _config_file_lock:
            config = await asyncio.to_thread(_load_xray_config_file, XRAY_CONFIG_PATH)
            
            # Remove from ALL inbounds (both basic and plus)
            all_vless = [ib for ib in config.get("inbounds", []) if ib.get("protocol") == "vless"]
            modified = _remove_client_from_inbounds(all_vless, target_uuid)
            
            if modified == 0:
                logger.warning(f"Client not found in any inbound: uuid={target_uuid}")
                return RemoveUserResponse(status="ok")
            
            logger.info(f"Client removed from {modified} inbounds: uuid={target_uuid}")
            await asyncio.to_thread(_save_xray_config_file, config, XRAY_CONFIG_PATH)
        
        _mark_restart_pending("remove_user")
        
        logger.info(f"User removed successfully: uuid={target_uuid}")
        
        return RemoveUserResponse(status="ok")
        
    except asyncio.CancelledError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("XRAY_API_ERROR")
        raise HTTPException(status_code=500, detail="internal_error")


@app.post("/update-user", response_model=UpdateUserResponse)
async def update_user(request: UpdateUserRequest):
    """
    Обновить expiryTime существующего клиента в Xray.
    Если клиент не найден — воссоздать с тем же UUID (fallback add). Никогда не возвращать 404.
    """
    try:
        # UUID_AUDIT_API_RECEIVED: Trace UUID received at update-user API
        logger.info(f"UUID_AUDIT_API_RECEIVED [request.uuid={repr(request.uuid)}]")
        # UUID used exactly as received. No transformation.
        target_uuid = request.uuid.strip()
        
        if not validate_uuid(target_uuid):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid UUID format: {target_uuid}"
            )
        
        logger.info(
            f"Updating user: uuid={target_uuid}, "
            f"expiry_timestamp_ms={request.expiry_timestamp_ms}"
        )
        
        async with _config_file_lock:
            config = await asyncio.to_thread(_load_xray_config_file, XRAY_CONFIG_PATH)
            
            # Update expiry in ALL inbounds where this client exists
            all_vless = [ib for ib in config.get("inbounds", []) if ib.get("protocol") == "vless"]
            client_found = False
            old_expiry = None
            
            for inbound in all_vless:
                clients = inbound.get("settings", {}).get("clients", [])
                for client in clients:
                    if client.get("id") == target_uuid:
                        if old_expiry is None:
                            old_expiry = client.get("expiryTime")
                        client["expiryTime"] = request.expiry_timestamp_ms
                        if "email" not in client:
                            client["email"] = f"uuid_{target_uuid[:8]}"
                        # Fix flow if needed
                        transport = _get_inbound_transport(inbound)
                        if transport == "tcp" and "flow" not in client:
                            client["flow"] = "xtls-rprx-vision"
                        elif transport != "tcp" and "flow" in client:
                            del client["flow"]
                        client_found = True
            
            if not client_found:
                # Fallback: add to basic inbounds
                logger.info(f"XRAY_UPDATE_FALLBACK_ADD uuid={target_uuid[:8]}... (client missing, recreating)")
                basic_inbounds = _get_inbounds_by_group(config, "basic")
                email = f"user_recovered_{target_uuid[:8]}"
                _add_client_to_inbounds(config, basic_inbounds, target_uuid, email, request.expiry_timestamp_ms)
            else:
                logger.info(f"XRAY_UPDATE uuid={target_uuid[:8]}... old_expiry={old_expiry} new_expiry={request.expiry_timestamp_ms}")
            
            await asyncio.to_thread(_save_xray_config_file, config, XRAY_CONFIG_PATH)
        
        _mark_restart_pending("update_user")
        
        return UpdateUserResponse(status="ok")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("XRAY_API_ERROR")
        raise HTTPException(status_code=500, detail="internal_error")


@app.post("/upgrade-to-plus/{uuid}")
async def upgrade_to_plus(uuid: str):
    """
    Upgrade user from basic to plus: add to all plus inbounds.
    Basic inbounds are NOT touched (user stays there).
    Returns basic_link and plus_link.
    """
    try:
        target_uuid = uuid.strip()
        if not validate_uuid(target_uuid):
            raise HTTPException(status_code=400, detail=f"Invalid UUID format: {target_uuid}")
        
        logger.info(f"Upgrading user to plus: uuid={target_uuid[:8]}...")
        
        async with _config_file_lock:
            config = await asyncio.to_thread(_load_xray_config_file, XRAY_CONFIG_PATH)
            
            # Check user exists in at least one basic inbound
            basic_inbounds = _get_inbounds_by_group(config, "basic")
            user_exists = False
            expiry_ms = 0
            email = f"user_{target_uuid[:8]}"
            
            for inbound in basic_inbounds:
                for client in inbound.get("settings", {}).get("clients", []):
                    if client.get("id") == target_uuid:
                        user_exists = True
                        expiry_ms = client.get("expiryTime", 0)
                        email = client.get("email", email)
                        break
                if user_exists:
                    break
            
            if not user_exists:
                raise HTTPException(status_code=404, detail="User not found in basic inbounds")
            
            # Add to all plus inbounds
            plus_inbounds = _get_inbounds_by_group(config, "plus")
            modified = _add_client_to_inbounds(config, plus_inbounds, target_uuid, email, expiry_ms)
            logger.info(f"UPGRADE_TO_PLUS uuid={target_uuid[:8]}... plus_inbounds_modified={modified}")
            
            await asyncio.to_thread(_save_xray_config_file, config, XRAY_CONFIG_PATH)
        
        _mark_restart_pending("upgrade_to_plus")
        
        basic_link = generate_vless_link(target_uuid)
        # For plus_link, generate link using first plus inbound's settings
        # Bot uses subscription URL from mini app anyway, so this is just a reference
        plus_link = basic_link.replace(f":{XRAY_PORT}", ":4445").replace("#AtlasSecure", "#AtlasPlus")
        
        return {
            "uuid": target_uuid,
            "tariff": "plus",
            "basic_link": basic_link,
            "plus_link": plus_link,
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("XRAY_API_ERROR upgrade_to_plus")
        raise HTTPException(status_code=500, detail="internal_error")


@app.post("/remove-plus/{uuid}")
async def remove_plus(uuid: str):
    """
    Remove user from all plus inbounds only. Basic inbounds are NOT touched.
    Used when downgrading from plus to basic.
    Idempotent: returns ok even if user was not in plus inbounds.
    """
    try:
        target_uuid = uuid.strip()
        if not validate_uuid(target_uuid):
            raise HTTPException(status_code=400, detail=f"Invalid UUID format: {target_uuid}")
        
        logger.info(f"Removing user from plus inbounds: uuid={target_uuid[:8]}...")
        
        async with _config_file_lock:
            config = await asyncio.to_thread(_load_xray_config_file, XRAY_CONFIG_PATH)
            
            plus_inbounds = _get_inbounds_by_group(config, "plus")
            modified = _remove_client_from_inbounds(plus_inbounds, target_uuid)
            
            if modified > 0:
                logger.info(f"REMOVE_PLUS uuid={target_uuid[:8]}... inbounds_modified={modified}")
                await asyncio.to_thread(_save_xray_config_file, config, XRAY_CONFIG_PATH)
            else:
                logger.info(f"REMOVE_PLUS uuid={target_uuid[:8]}... not found in plus inbounds")
        
        if modified > 0:
            _mark_restart_pending("remove_plus")
        
        return {"status": "ok", "inbounds_modified": modified}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("XRAY_API_ERROR remove_plus")
        raise HTTPException(status_code=500, detail="internal_error")


@app.get("/list-users", response_model=ListUsersResponse)
async def list_users():
    """
    Return UUIDs of all VLESS clients in Xray config.
    Used by reconciliation worker to detect orphans (in Xray but not in DB).
    """
    try:
        config_data = await asyncio.to_thread(_load_xray_config_file, XRAY_CONFIG_PATH)
        seen: set[str] = set()
        uuids: list[str] = []
        for inbound in config_data.get("inbounds", []):
            if inbound.get("protocol") != "vless":
                continue
            for client in inbound.get("settings", {}).get("clients", []):
                cid = client.get("id")
                if cid and validate_uuid(cid) and cid not in seen:
                    seen.add(cid)
                    uuids.append(cid)
        return ListUsersResponse(uuids=uuids)
    except Exception as e:
        logger.exception("XRAY_API_ERROR list-users")
        raise HTTPException(status_code=500, detail="internal_error")


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

