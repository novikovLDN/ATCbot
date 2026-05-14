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


async def _request_raw(
    method: str,
    path: str,
    **kwargs,
) -> Dict[str, Any]:
    """Like _request, but always returns a structured envelope so the caller
    can distinguish HTTP failure modes.

    Returns:
        {"ok": bool, "status": int, "body": parsed-json-or-text, "response": unwrapped-or-None}
    """
    url = f"{config.REMNAWAVE_API_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.request(method, url, headers=_headers(), **kwargs)
    except httpx.TimeoutException:
        logger.error("REMNAWAVE_TIMEOUT: %s %s", method, path)
        return {"ok": False, "status": 0, "body": None, "response": None, "error": "timeout"}
    except Exception as e:
        logger.error("REMNAWAVE_ERROR: %s %s %s: %s", method, path, type(e).__name__, e)
        return {"ok": False, "status": 0, "body": None, "response": None, "error": str(e)}

    try:
        body: Any = resp.json()
    except Exception:
        body = resp.text

    unwrapped = body["response"] if isinstance(body, dict) and "response" in body else body
    ok = resp.status_code < 400
    if not ok:
        # Only log at warning level — caller decides whether it is fatal.
        logger.warning(
            "REMNAWAVE_HTTP_%s: %s %s body=%s",
            resp.status_code, method, path, str(body)[:500],
        )
    return {"ok": ok, "status": resp.status_code, "body": body, "response": unwrapped}


# ── User CRUD ──────────────────────────────────────────────────────────

async def create_user(
    username: str,
    short_uuid: str,
    traffic_limit_bytes: int,
    expire_at: str,
    device_limit: int = 3,
    *,
    uuid: Optional[str] = None,
    squad_uuid: Optional[str] = None,
    description: Optional[str] = None,
    telegram_id: Optional[int] = None,
    traffic_limit_strategy: str = "NO_RESET",
    raw_response: bool = False,
) -> Optional[Dict[str, Any]]:
    """POST /api/users — create a new Remnawave user.

    Extra keyword args (added for the samopis→premium migration):
      uuid                 — VLESS UUID to force.  On Remnawave v2.7+ the
                             panel separates entity into `uuid` (panel-internal,
                             always panel-assigned) and `vlessUuid` (used in
                             VLESS connection strings).  When this param is
                             supplied the value is sent in the `vlessUuid`
                             field so legacy samopis links keep working on the
                             new inbounds.  Callers MUST read
                             result['vlessUuid'] to learn whether the panel
                             honoured the request and result['uuid'] for the
                             internal identifier used by subsequent API calls.
      squad_uuid           — override config.REMNAWAVE_SQUAD_UUID (e.g. the
                             "MainServer" squad for the premium tier).  Pass
                             "" to skip the default-squad assignment entirely.
      description          — passed through to Remnawave (e.g. "Imported from
                             samopis vpnapi").
      telegram_id          — passed through as `telegramId` for panel-side
                             cross-reference.
      traffic_limit_strategy — Remnawave reset strategy (default NO_RESET).
      raw_response         — when True the caller wants the HTTP status code
                             alongside the body to disambiguate 409/400
                             responses (used by the migration script).
    """
    body: Dict[str, Any] = {
        "username": username,
        "shortUuid": short_uuid,
        "trafficLimitBytes": traffic_limit_bytes,
        "trafficLimitStrategy": traffic_limit_strategy,
        "status": "ACTIVE",
        "expireAt": expire_at,
        "deviceLimit": device_limit,
    }
    if uuid:
        # Remnawave v2.7+ moved the connection UUID to `vlessUuid` —
        # `uuid` is panel-assigned and cannot be overridden.  See module
        # docstring on find_user_by_username.
        body["vlessUuid"] = uuid
    if description:
        body["description"] = description
    if telegram_id is not None:
        body["telegramId"] = int(telegram_id)

    # Squad assignment: explicit param wins, "" disables it, None falls back to
    # the global default (existing bypass behaviour).
    if squad_uuid is None:
        effective_squad = config.REMNAWAVE_SQUAD_UUID
    else:
        effective_squad = squad_uuid
    if effective_squad:
        body["activeInternalSquads"] = [effective_squad]

    if raw_response:
        return await _request_raw("POST", "/api/users", json=body)

    result = await _request("POST", "/api/users", json=body)
    if result:
        logger.info(
            "REMNAWAVE_CREATE: success for %s, response keys=%s squad_in_response=%s",
            username, list(result.keys()),
            result.get("activeInternalSquads"),
        )

        # Also try dedicated squad endpoint (belt-and-suspenders)
        if effective_squad:
            user_uuid = result.get("uuid")
            if user_uuid:
                squad_result = result.get("activeInternalSquads") or []
                if not squad_result:
                    logger.warning(
                        "REMNAWAVE_SQUAD_NOT_IN_RESPONSE: user=%s, trying assign_user_to_squad",
                        user_uuid[:8],
                    )
                    await assign_user_to_squad(user_uuid, effective_squad)
        else:
            logger.warning("REMNAWAVE_SQUAD_UUID not set — skipping squad assignment")
    else:
        logger.warning("REMNAWAVE_CREATE: failed for %s", username)
    return result


async def assign_user_to_squad(user_uuid: str, squad_uuid: str) -> bool:
    """Try multiple approaches to assign user to a squad."""
    logger.info(
        "REMNAWAVE_SQUAD_ASSIGN_START: user=%s squad=%s",
        user_uuid[:8], squad_uuid[:8],
    )

    # Approach 1: POST /api/squads/add-users-to-squad (Remnawave standard)
    result = await _request(
        "POST", "/api/squads/add-users-to-squad",
        quiet=True,
        json={"squadUuid": squad_uuid, "userUuids": [user_uuid]},
    )
    if result is not None:
        logger.info("REMNAWAVE_SQUAD_ASSIGN: via /api/squads/add-users-to-squad user=%s", user_uuid[:8])
        return True

    # Approach 2: POST /api/squads/{squad_uuid}/users
    result = await _request(
        "POST", f"/api/squads/{squad_uuid}/users",
        quiet=True,
        json={"userUuid": user_uuid},
    )
    if result is not None:
        logger.info("REMNAWAVE_SQUAD_ASSIGN: via /api/squads/.../users user=%s", user_uuid[:8])
        return True

    # Approach 3: POST /api/squads/{squad_uuid}/users with array body
    result = await _request(
        "POST", f"/api/squads/{squad_uuid}/users",
        quiet=True,
        json={"userUuids": [user_uuid]},
    )
    if result is not None:
        logger.info("REMNAWAVE_SQUAD_ASSIGN: via /api/squads/.../users array user=%s", user_uuid[:8])
        return True

    # Approach 4: PUT user update with activeInternalSquads
    body = {"uuid": user_uuid, "activeInternalSquads": [squad_uuid]}
    for method in ("PATCH", "POST", "PUT"):
        for path in ("/api/users", "/api/users/update"):
            r = await _request(method, path, quiet=True, json=body)
            if r is not None:
                # Verify squad was actually set
                check = await get_user(user_uuid)
                if check and check.get("activeInternalSquads"):
                    logger.info(
                        "REMNAWAVE_SQUAD_ASSIGN: via %s %s user=%s",
                        method, path, user_uuid[:8],
                    )
                    return True

    logger.error(
        "REMNAWAVE_SQUAD_ASSIGN_FAILED: all approaches failed user=%s squad=%s",
        user_uuid[:8], squad_uuid[:8],
    )
    return False


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


# ── Username search (preflight for the samopis migration) ──────────────
#
# Remnawave v2.7.4 (this deployment) exposes a dedicated endpoint:
#   GET /api/users/by-username/{username}
#     → 200 + user entity   (username taken)
#     → 404 + errorCode A063 ("User with specified params not found")
# No pagination / list-fallback is needed.  If the dedicated endpoint
# ever disappears in a future panel version the migration script will
# fail loudly via the "unexpected_http_status" code path and we'll
# know to add a fallback again.


async def find_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """Return the user entity whose `username` matches, or None if free.

    Confirmed working on Remnawave v2.7.4.  Returns None on any
    non-200/404 status so that callers can decide whether to retry — the
    raw HTTP status is logged at WARN level for diagnostics.
    """
    if not username:
        return None
    from urllib.parse import quote
    path = f"/api/users/by-username/{quote(username, safe='')}"
    raw = await _request_raw("GET", path)
    status = int(raw.get("status") or 0)
    if status == 200 and isinstance(raw.get("response"), dict):
        return raw["response"]
    if status == 404:
        # errorCode A063 is the expected "no such user" body — username is free.
        return None
    # Anything else: transient or unexpected.  Don't claim the username is
    # free (could be a transient panel hiccup); return None and let the
    # caller decide whether to proceed with POST.
    logger.warning(
        "REMNAWAVE_FIND_UNEXPECTED_STATUS: username=%s status=%s body=%s",
        username, status, str(raw.get("body") or "")[:200],
    )
    return None


# ── Convenience ───────────────────────────────────────────────────────

# Possible field names the panel may use for the encrypted link in its
# response.  Documented (TZ Task 4): `encryptedLink`.  Defensive list
# covers the natural variations seen on minor v2.7 versions / forks.
_HAPP_CRYPTO_LINK_FIELDS = (
    "encryptedLink",
    "encrypted_link",
    "cryptoLink",
    "crypto_link",
    "happLink",
    "happ_link",
    "link",
    "data",
    "url",
)

# Endpoint paths to try in order.  v2.7.4 was documented at the first
# path; the rest are observed in panel forks / older versions.  The
# winning path is cached at module level so retries don't redo the probe.
_HAPP_CRYPTO_ENDPOINTS = (
    "/api/system/encrypt-happ-crypto-link",
    "/api/system/happ-crypto-link",
    "/api/system/encrypt-link",
    "/api/users/encrypt-happ-link",
)

_happ_crypto_endpoint_cache: Optional[str] = None


def _extract_happ_crypto_link(payload: Any) -> Optional[str]:
    """Pull a `happ://crypto/...` value out of a panel response no matter
    where the field lives.  Walks the dict for known keys first, then
    falls back to a substring search on any string value.
    """
    if payload is None:
        return None
    if isinstance(payload, str):
        # Some panels return the raw link.
        stripped = payload.strip()
        return stripped if stripped.startswith("happ://crypto/") else None
    if not isinstance(payload, dict):
        return None
    # 1) Known field names — case-insensitive match on keys.
    lower_map = {str(k).lower(): v for k, v in payload.items()}
    for field in _HAPP_CRYPTO_LINK_FIELDS:
        v = lower_map.get(field.lower())
        if isinstance(v, str):
            stripped = v.strip()
            if stripped.startswith("happ://crypto/"):
                return stripped
    # 2) Nested `response` wrapper (defence in depth — _request usually
    #    unwraps this already, but some endpoints double-wrap).
    if isinstance(payload.get("response"), (dict, str)):
        nested = _extract_happ_crypto_link(payload["response"])
        if nested:
            return nested
    # 3) Substring search across all string values.  Last resort but
    #    catches arbitrary field names we haven't enumerated.
    for v in payload.values():
        if isinstance(v, str) and "happ://crypto/" in v:
            # Trim everything before the scheme + everything after the
            # first whitespace, just in case the panel padded the field.
            idx = v.find("happ://crypto/")
            tail = v[idx:].split()[0]
            return tail
    return None


async def encrypt_happ_crypto_link(plain_subscription_url: str) -> Optional[str]:
    """Encrypt a plain Remnawave subscription URL into a `happ://crypto/...`
    deeplink via the panel.

    Returns the encrypted link on success, None on any failure /
    malformed response.  Callers fall back to the plain URL.

    Implementation details:
      * Tries the documented endpoint first
        (`/api/system/encrypt-happ-crypto-link` on v2.7+).
      * If the first call 404s we transparently try a small list of
        alternative paths observed on minor versions / forks.  The
        winning path is cached at module level so subsequent calls go
        straight to it.
      * The response parser is intentionally lenient: looks for the
        link under several possible field names (`encryptedLink`,
        `cryptoLink`, `link`, `data`, …) and finally falls back to a
        substring search.  This protects against schema drift between
        panel versions.
      * Logs a one-line summary every call so operators can see
        which endpoint won and what shape the panel returned, without
        leaking the plain URL or the encrypted payload.
    """
    global _happ_crypto_endpoint_cache
    if not plain_subscription_url:
        return None

    body = {"data": plain_subscription_url}
    paths_to_try: list[str]
    if _happ_crypto_endpoint_cache:
        paths_to_try = [_happ_crypto_endpoint_cache]
    else:
        paths_to_try = list(_HAPP_CRYPTO_ENDPOINTS)

    last_status: Optional[int] = None
    last_response_keys: Optional[list[str]] = None

    for path in paths_to_try:
        raw = await _request_raw("POST", path, json=body)
        status = int(raw.get("status") or 0)
        last_status = status
        if status == 404:
            # Path missing — try the next one in the list.  Skip silently;
            # _request_raw already warning-logged the 404 body.
            continue
        if not raw.get("ok"):
            # Real panel error (5xx / auth / etc.) — bail with a single
            # error log so the operator can see what shape came back.
            logger.error(
                "REMNAWAVE_HAPP_CRYPTO_PANEL_ERROR: path=%s status=%s body=%s",
                path, status, str(raw.get("body") or "")[:300],
            )
            return None

        response = raw.get("response")
        if isinstance(response, dict):
            last_response_keys = sorted(response.keys())
        link = _extract_happ_crypto_link(response)
        if link:
            # Cache the working path for subsequent calls.
            if _happ_crypto_endpoint_cache != path:
                _happ_crypto_endpoint_cache = path
                logger.info(
                    "REMNAWAVE_HAPP_CRYPTO_ENDPOINT_CACHED: path=%s response_keys=%s",
                    path, last_response_keys,
                )
            return link

        # 200 OK but no recognisable link in the body — log and try the
        # next path.  This is the case where the panel returns a
        # different schema we haven't covered.
        logger.warning(
            "REMNAWAVE_HAPP_CRYPTO_UNEXPECTED_SHAPE: path=%s status=%s response_keys=%s body=%s",
            path, status, last_response_keys, str(raw.get("body") or "")[:300],
        )

    # All paths exhausted without producing a usable link.
    logger.warning(
        "REMNAWAVE_HAPP_CRYPTO_UNAVAILABLE: last_status=%s last_keys=%s — "
        "user will be served the plain URL as fallback",
        last_status, last_response_keys,
    )
    return None


def _reset_happ_crypto_endpoint_cache_for_tests() -> None:
    global _happ_crypto_endpoint_cache
    _happ_crypto_endpoint_cache = None


async def get_user_traffic(uuid: str) -> Optional[Dict[str, Any]]:
    """Return traffic info including subscriptionUrl and happ_url, or None."""
    user = await get_user(uuid)
    if not user:
        return None
    # Traffic data may be nested in userTraffic or at top level
    user_traffic = user.get("userTraffic") or {}
    sub_url = user.get("subscriptionUrl", "")
    return {
        "usedTrafficBytes": user_traffic.get("usedTrafficBytes", user.get("usedTrafficBytes", 0)),
        "trafficLimitBytes": user.get("trafficLimitBytes", 0),
        "deviceLimit": user.get("deviceLimit", 0),
        "onlineDevices": user.get("onlineDevices", 0),
        "status": user.get("status", "UNKNOWN"),
        "subscriptionUrl": sub_url,
        "happ_url": f"happ://add/{sub_url}" if sub_url else "",
    }


