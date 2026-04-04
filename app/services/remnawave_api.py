"""
HTTP client for Remnawave Panel API (Yandex node with traffic limits).

Base URL: config.REMNAWAVE_API_URL  (e.g. https://rmnw.atlassecure.ru)
Auth:     Authorization: Bearer <REMNAWAVE_API_TOKEN>

Endpoints used:
    POST   /api/users              — create user with traffic limit
    GET    /api/users/<shortUuid>  — get user info (traffic used/limit)
    PATCH  /api/users/<shortUuid>  — update traffic limit / status / expiry
    DELETE /api/users/<shortUuid>  — delete user
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

import config

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {config.REMNAWAVE_API_TOKEN}",
        "Content-Type": "application/json",
    }


def _base_url() -> str:
    return config.REMNAWAVE_API_URL.rstrip("/")


# =========================================================================
# Low-level request helper
# =========================================================================

async def _request(
    method: str,
    path: str,
    *,
    json: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Make an HTTP request to Remnawave Panel API.
    Returns parsed JSON dict on success, None on error.
    """
    if not config.REMNAWAVE_ENABLED:
        logger.debug("REMNAWAVE: disabled, skipping %s %s", method, path)
        return None

    url = f"{_base_url()}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.request(method, url, headers=_headers(), json=json)

        if resp.status_code in (200, 201):
            data = resp.json()
            # Remnawave wraps in {"response": {...}} for some endpoints
            if isinstance(data, dict) and "response" in data:
                return data["response"]
            return data

        if resp.status_code == 404:
            logger.warning("REMNAWAVE: 404 %s %s", method, url)
            return None

        logger.error(
            "REMNAWAVE: %s %s → %d: %s",
            method, url, resp.status_code, resp.text[:300],
        )
        return None

    except httpx.TimeoutException:
        logger.error("REMNAWAVE: timeout %s %s", method, url)
        return None
    except Exception as e:
        logger.exception("REMNAWAVE: error %s %s: %s", method, url, e)
        return None


# =========================================================================
# Public API
# =========================================================================

async def create_user(
    username: str,
    short_uuid: str,
    traffic_limit_bytes: int,
    expire_at: datetime,
) -> Optional[Dict[str, Any]]:
    """
    Create a user in Remnawave with traffic limit.

    Args:
        username: telegram_id as string
        short_uuid: VPN UUID (same as Xray UUID)
        traffic_limit_bytes: traffic limit in bytes
        expire_at: subscription expiry (UTC datetime)

    Returns:
        User dict from Remnawave or None on error.
    """
    expire_str = expire_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    body: Dict[str, Any] = {
        "username": username,
        "shortUuid": short_uuid,
        "trafficLimitBytes": traffic_limit_bytes,
        "trafficLimitStrategy": "NO_RESET",
        "status": "ACTIVE",
        "expireAt": expire_str,
    }
    if config.REMNAWAVE_INBOUND_UUID:
        body["activeUserInbounds"] = [config.REMNAWAVE_INBOUND_UUID]
    logger.info(
        "REMNAWAVE_CREATE: username=%s uuid=%s limit=%d bytes expire=%s",
        username, short_uuid[:8], traffic_limit_bytes, expire_str,
    )
    result = await _request("POST", "/api/users", json=body)
    if result:
        logger.info("REMNAWAVE_CREATE: success for %s", username)
    else:
        logger.warning("REMNAWAVE_CREATE: failed for %s", username)
    return result


async def get_user(short_uuid: str) -> Optional[Dict[str, Any]]:
    """
    Get user info from Remnawave (includes usedTrafficBytes, trafficLimitBytes).
    Returns None if user not found or error.
    """
    return await _request("GET", f"/api/users/{short_uuid}")


async def update_user(
    short_uuid: str,
    *,
    traffic_limit_bytes: Optional[int] = None,
    status: Optional[str] = None,
    expire_at: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """
    Update user in Remnawave (traffic limit, status, expiry).
    Only sends fields that are provided.
    """
    body: Dict[str, Any] = {}
    if traffic_limit_bytes is not None:
        body["trafficLimitBytes"] = traffic_limit_bytes
    if status is not None:
        body["status"] = status
    if expire_at is not None:
        body["expireAt"] = expire_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    if not body:
        return None

    logger.info("REMNAWAVE_UPDATE: uuid=%s body=%s", short_uuid[:8], body)
    return await _request("PATCH", f"/api/users/{short_uuid}", json=body)


async def reset_user_traffic(short_uuid: str) -> Optional[Dict[str, Any]]:
    """
    Reset used traffic counter for a user.
    Remnawave API: POST /api/users/<shortUuid>/reset-traffic
    """
    logger.info("REMNAWAVE_RESET_TRAFFIC: uuid=%s", short_uuid[:8])
    return await _request("POST", f"/api/users/{short_uuid}/reset-traffic")


async def delete_user(short_uuid: str) -> Optional[Dict[str, Any]]:
    """
    Delete user from Remnawave. Idempotent (404 = already deleted).
    """
    logger.info("REMNAWAVE_DELETE: uuid=%s", short_uuid[:8])
    return await _request("DELETE", f"/api/users/{short_uuid}")


async def get_user_traffic(short_uuid: str) -> Optional[Dict[str, int]]:
    """
    Get traffic stats for a user.

    Returns:
        {"usedTrafficBytes": int, "trafficLimitBytes": int} or None
    """
    user = await get_user(short_uuid)
    if not user:
        return None
    return {
        "usedTrafficBytes": user.get("usedTrafficBytes", 0),
        "trafficLimitBytes": user.get("trafficLimitBytes", 0),
    }
