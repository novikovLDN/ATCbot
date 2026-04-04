"""
Low-level HTTP client for Remnawave Panel API.

All methods return parsed JSON dict on success, None on failure.
Errors are logged but never raised — callers must check for None.
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
            logger.warning("REMNAWAVE_404: %s %s", method, path)
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
    result = await _request("POST", "/api/users", json=body)
    if result:
        logger.info("REMNAWAVE_CREATE: success for %s, response=%s", username, result)
    else:
        logger.warning("REMNAWAVE_CREATE: failed for %s", username)
    return result


async def get_user(uuid: str) -> Optional[Dict[str, Any]]:
    """GET /api/users/{uuid}"""
    return await _request("GET", f"/api/users/{uuid}")


async def update_user(uuid: str, **fields) -> Optional[Dict[str, Any]]:
    """PATCH /api/users/{uuid} — update user fields."""
    return await _request("PATCH", f"/api/users/{uuid}", json=fields)


async def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """POST /api/users/by-username — look up user by username."""
    return await _request("POST", "/api/users/by-username", json={"username": username})


async def get_user_by_short_uuid(short_uuid: str) -> Optional[Dict[str, Any]]:
    """POST /api/users/by-short-uuid — look up user by shortUuid."""
    return await _request("POST", "/api/users/by-short-uuid", json={"shortUuid": short_uuid})


async def reset_user_traffic(uuid: str) -> Optional[Dict[str, Any]]:
    """POST /api/users/{uuid}/reset-traffic"""
    return await _request("POST", f"/api/users/{uuid}/reset-traffic")


async def delete_user(uuid: str) -> Optional[Dict[str, Any]]:
    """DELETE /api/users/{uuid}"""
    return await _request("DELETE", f"/api/users/{uuid}")


# ── Convenience ────���───────────────────────────────────────────────────

async def get_user_traffic(uuid: str) -> Optional[Dict[str, Any]]:
    """Return {usedTrafficBytes, trafficLimitBytes, deviceLimit, onlineDevices} or None."""
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
    }
