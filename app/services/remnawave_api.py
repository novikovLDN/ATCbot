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
    quiet: bool = False,
    **kwargs,
) -> Optional[Dict[str, Any]]:
    """Send request to Remnawave API and unwrap {response: ...} envelope."""
    url = f"{config.REMNAWAVE_API_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.request(method, url, headers=_headers(), **kwargs)

        if resp.status_code == 404:
            if not quiet:
                logger.warning("REMNAWAVE_404: %s %s body=%s", method, path, resp.text[:500])
            return None

        if resp.status_code >= 400:
            if not quiet:
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


_update_method: Optional[tuple] = None  # cached working (method, path_template)

async def update_user(uuid: str, **fields) -> Optional[Dict[str, Any]]:
    """Update user fields. Auto-discovers the correct endpoint on first call."""
    global _update_method
    body = {"uuid": uuid, **fields}

    # Use cached method if already discovered
    if _update_method:
        method, path_tpl = _update_method
        path = path_tpl.format(uuid=uuid)
        return await _request(method, path, json=body)

    # Probe all known Remnawave panel endpoint variants
    _variants = [
        ("PUT", "/api/users/{uuid}"),
        ("POST", "/api/users/{uuid}"),
        ("PATCH", "/api/users"),
        ("POST", "/api/users/update"),
        ("PUT", "/api/users"),
    ]
    for method, path_tpl in _variants:
        path = path_tpl.format(uuid=uuid)
        result = await _request(method, path, quiet=True, json=body)
        if result is not None:
            _update_method = (method, path_tpl)
            logger.info("REMNAWAVE_UPDATE_DISCOVERED: %s %s works, caching", method, path_tpl)
            return result

    logger.error(
        "REMNAWAVE_UPDATE_FAIL: no endpoint worked for uuid=%s fields=%s. "
        "Tried: %s", uuid[:8], list(fields.keys()),
        [(m, p) for m, p in _variants],
    )
    return None


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
