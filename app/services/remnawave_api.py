"""
Low-level HTTP client for Remnawave Panel API.

All methods return parsed JSON dict on success, None on failure.
Errors are logged but never raised — callers must check for None.

Verified endpoints on this panel instance:
- POST   /api/users                       — create user (201)
- GET    /api/users/{uuid}                — get by full UUID (400 if invalid)
- POST   /api/users/update                — update user fields (uuid in body)
- DELETE /api/users/{uuid}                — delete user
- POST   /api/users/{uuid}/reset-traffic  — reset traffic counter
"""
import logging
from typing import Optional, Dict, Any

import httpx
import config

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {config.REMNAWAVE_API_TOKEN}",
        "Content-Type": "application/json",
    }


async def _request(
    method: str,
    path: str,
    **kwargs,
) -> Optional[Dict[str, Any]]:
    """Send request to Remnawave API and unwrap {response: ...} envelope."""
    url = f"{config.REMNAWAVE_API_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.request(method, url, headers=_headers(), **kwargs)

        if resp.status_code == 404:
            logger.warning("REMNAWAVE_404: %s %s body=%s", method, path, resp.text[:500])
            return None

        if resp.status_code >= 400:
            logger.error(
                "REMNAWAVE_HTTP_%s: %s %s body=%s",
                resp.status_code, method, path, resp.text[:500],
            )
            return None

        data = resp.json()
        # Remnawave wraps successful responses in {"response": {...}}
        if isinstance(data, dict) and "response" in data:
            return data["response"]
        return data

    except httpx.TimeoutException:
        logger.error("REMNAWAVE_TIMEOUT: %s %s", method, path)
    except Exception as e:
        logger.error("REMNAWAVE_ERROR: %s %s %s: %s", method, path, type(e).__name__, e)
    return None


# ── User CRUD ──────────────────────────────────────────────────────────

async def create_user(
    username: str,
    short_uuid: str,
    traffic_limit_bytes: int,
    expire_at: str,
    device_limit: int = 3,
) -> Optional[Dict[str, Any]]:
    """POST /api/users — create a new Remnawave user."""
    body = {
        "username": username,
        "shortUuid": short_uuid,
        "trafficLimitBytes": traffic_limit_bytes,
        "trafficLimitStrategy": "NO_RESET",
        "status": "ACTIVE",
        "expireAt": expire_at,
        "deviceLimit": device_limit,
    }
    # Assign to "Clients" squad if configured
    if config.REMNAWAVE_SQUAD_UUID:
        body["activeInternalSquads"] = [config.REMNAWAVE_SQUAD_UUID]
    result = await _request("POST", "/api/users", json=body)
    if result:
        logger.info("REMNAWAVE_CREATE: success for %s, response=%s", username, result)
    else:
        logger.warning("REMNAWAVE_CREATE: failed for %s", username)
    return result


async def get_user(uuid: str) -> Optional[Dict[str, Any]]:
    """GET /api/users/{uuid} — get user by full UUID."""
    return await _request("GET", f"/api/users/{uuid}")


async def update_user(uuid: str, **fields) -> Optional[Dict[str, Any]]:
    """POST /api/users/update — update user fields (uuid in body)."""
    body = {"uuid": uuid, **fields}
    return await _request("POST", "/api/users/update", json=body)


async def reset_user_traffic(uuid: str) -> Optional[Dict[str, Any]]:
    """POST /api/users/{uuid}/reset-traffic"""
    return await _request("POST", f"/api/users/{uuid}/reset-traffic")


async def delete_user(uuid: str) -> Optional[Dict[str, Any]]:
    """DELETE /api/users/{uuid}"""
    return await _request("DELETE", f"/api/users/{uuid}")


# ── Convenience ───────────────────────────────────────────────────────

async def get_user_traffic(uuid: str) -> Optional[Dict[str, Any]]:
    """Return traffic info including subscriptionUrl, or None."""
    user = await get_user(uuid)
    if not user:
        return None
    # Traffic data may be nested in userTraffic or at top level
    user_traffic = user.get("userTraffic") or {}
    return {
        "usedTrafficBytes": user_traffic.get("usedTrafficBytes", user.get("usedTrafficBytes", 0)),
        "trafficLimitBytes": user.get("trafficLimitBytes", 0),
        "deviceLimit": user.get("deviceLimit", 0),
        "onlineDevices": user.get("onlineDevices", 0),
        "status": user.get("status", "UNKNOWN"),
        "subscriptionUrl": user.get("subscriptionUrl", ""),
    }
