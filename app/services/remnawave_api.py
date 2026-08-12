"""
Low-level HTTP client for Remnawave Panel API.

**Panel version:** Remnawave 3.x.

Breaking change vs 2.x: the panel identifies a user by a numeric `id`
(BigInt), not by `uuid`.  The `uuid` field is REMOVED from the user
response object; only `id`, `shortUuid`, `username` remain.

All endpoints that used to be `/api/users/{uuid}/...` are now
`/api/users/{user_id}/...`.  Bulk endpoints now expect `userIds: [int, ...]`
instead of `uuids: [str, ...]`.

To keep the transition small, the low-level helpers in this module now
accept EITHER a numeric id (int or numeric str) OR a legacy UUID
string.  A UUID string is logged as a deprecation warning and returned
as None — callers must resolve the numeric id via
`app.services.remnawave_id_resolver.get_remnawave_id_for(tg, kind)`
before calling into the API.

All methods return parsed JSON dict on success, None on failure.
Errors are logged but never raised — callers must check for None.
"""
import logging
from typing import Any, Dict, List, Optional, Union

import httpx
import config

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)

# Type alias — everywhere we accept "panel identifier for one user".
UserRef = Union[int, str, None]


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {config.REMNAWAVE_API_TOKEN}",
        "Content-Type": "application/json",
    }


def _looks_like_uuid(s: str) -> bool:
    return len(s) == 36 and s.count("-") == 4


def normalize_user_id(user_ref: UserRef) -> Optional[int]:
    """Normalize a caller-supplied identifier to Remnawave 3.x numeric id.

    Accepts:
      - int → returned as-is (must be positive).
      - str of digits ('12345') → int().
      - str that looks like a full UUID → logs a deprecation warning and
        returns None.  Remnawave 3.x cannot resolve full UUIDs anymore.
      - None / anything else → None.
    """
    if user_ref is None:
        return None
    if isinstance(user_ref, int):
        return user_ref if user_ref > 0 else None
    if isinstance(user_ref, bool):  # bool is subclass of int in Python
        return None
    try:
        s = str(user_ref).strip()
    except Exception:
        return None
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        pass
    if _looks_like_uuid(s):
        logger.warning(
            "REMNAWAVE_LEGACY_UUID_PASSED: user_ref=%s... — Remnawave 3.x "
            "requires numeric id; resolve via remnawave_id_resolver first.",
            s[:8],
        )
        return None
    logger.warning("REMNAWAVE_INVALID_USER_REF: %r", s)
    return None


def _log_typed_error(
    method: str, path: str, status: int, body: Any,
) -> None:
    """Best-effort structured logging of Remnawave 3.x typed errors.

    Panel 3.x returns 400s in shape `{"errors": [{path, code, message}, ...]}`
    and 404s in shape `{"errorCode": "A005", "message": "..."}`.  This helper
    surfaces those bits in the log so we don't have to eyeball the raw body.
    """
    if not isinstance(body, dict):
        return
    try:
        if status == 400 and isinstance(body.get("errors"), list):
            summaries = []
            for err in body["errors"][:5]:
                if isinstance(err, dict):
                    summaries.append(
                        f"{err.get('path','?')}:{err.get('code','?')} {err.get('message','')}"
                    )
            if summaries:
                logger.warning(
                    "REMNAWAVE_400_VALIDATION: %s %s errors=%s",
                    method, path, " | ".join(summaries),
                )
        elif status in (404, 409) and body.get("errorCode"):
            logger.warning(
                "REMNAWAVE_%s_TYPED: %s %s code=%s msg=%s",
                status, method, path,
                body.get("errorCode"), body.get("message", ""),
            )
    except Exception:
        pass


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

        # DELETE endpoints return 204 with empty body — treat as success.
        if resp.status_code == 204:
            return {"success": True}

        if resp.status_code == 404:
            if not quiet:
                logger.warning("REMNAWAVE_404: %s %s body=%s", method, path, resp.text[:500])
            try:
                _log_typed_error(method, path, 404, resp.json())
            except Exception:
                pass
            return None

        if resp.status_code >= 400:
            body_text = resp.text[:500]
            if not quiet:
                logger.error(
                    "REMNAWAVE_HTTP_%s: %s %s body=%s",
                    resp.status_code, method, path, body_text,
                )
            try:
                _log_typed_error(method, path, resp.status_code, resp.json())
            except Exception:
                pass
            return None

        # 202 (Accepted) — bulk endpoints return no body.
        if resp.status_code == 202 or not resp.content:
            return {"success": True}

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

    # 204 → success without body
    if resp.status_code == 204:
        return {"ok": True, "status": 204, "body": None, "response": {"success": True}}

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
        _log_typed_error(method, path, resp.status_code, body)
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
    external_squad_uuid: Optional[str] = None,
    raw_response: bool = False,
) -> Optional[Dict[str, Any]]:
    """POST /api/users — create a new Remnawave user.

    Panel 3.x response contains numeric `id` (BigInt), `shortUuid`,
    `subscriptionUrl`, `vlessUuid` — no `uuid` field.  Callers must
    persist `id` (the new column `remnawave_id[_premium]`) and can
    still use `shortUuid` for by-short-uuid fallback resolution.

    Extra keyword args are unchanged from 2.x.
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
        # Remnawave 3.x still accepts `vlessUuid` on create for legacy VLESS
        # ключ compatibility (samopis migration).  Panel-side `id` is always
        # panel-assigned.
        body["vlessUuid"] = uuid
    if description:
        body["description"] = description
    if telegram_id is not None:
        body["telegramId"] = int(telegram_id)
    if external_squad_uuid:
        body["externalSquadUuid"] = external_squad_uuid

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
        panel_id = result.get("id")
        logger.info(
            "REMNAWAVE_CREATE: success for %s id=%s response keys=%s squad_in_response=%s",
            username, panel_id, list(result.keys()),
            result.get("activeInternalSquads"),
        )

        # Belt-and-suspenders squad assignment (retried via numeric id).
        if effective_squad and panel_id:
            squad_result = result.get("activeInternalSquads") or []
            if not squad_result:
                logger.warning(
                    "REMNAWAVE_SQUAD_NOT_IN_RESPONSE: user_id=%s, trying assign_user_to_squad",
                    panel_id,
                )
                await assign_user_to_squad(panel_id, effective_squad)
        elif not effective_squad:
            logger.warning("REMNAWAVE_SQUAD_UUID not set — skipping squad assignment")
    else:
        logger.warning("REMNAWAVE_CREATE: failed for %s", username)
    return result


async def assign_user_to_squad(user_ref: UserRef, squad_uuid: str) -> bool:
    """Try multiple approaches to assign a user to an internal squad."""
    user_id = normalize_user_id(user_ref)
    if user_id is None:
        return False

    logger.info(
        "REMNAWAVE_SQUAD_ASSIGN_START: user_id=%s squad=%s",
        user_id, squad_uuid[:8],
    )

    # Approach 1: POST /api/squads/add-users-to-squad — 3.x uses userIds.
    result = await _request(
        "POST", "/api/squads/add-users-to-squad",
        quiet=True,
        json={"squadUuid": squad_uuid, "userIds": [user_id]},
    )
    if result is not None:
        logger.info("REMNAWAVE_SQUAD_ASSIGN: via add-users-to-squad user_id=%s", user_id)
        return True

    # Approach 2: PATCH user with activeInternalSquads.
    body = {"activeInternalSquads": [squad_uuid]}
    for method in ("PATCH", "POST", "PUT"):
        for path in (f"/api/users/{user_id}", "/api/users", "/api/users/update"):
            payload = dict(body)
            if path in ("/api/users", "/api/users/update"):
                payload["userId"] = user_id
            r = await _request(method, path, quiet=True, json=payload)
            if r is not None:
                # Verify squad was actually set
                check = await get_user(user_id)
                if check and check.get("activeInternalSquads"):
                    logger.info(
                        "REMNAWAVE_SQUAD_ASSIGN: via %s %s user_id=%s",
                        method, path, user_id,
                    )
                    return True

    logger.error(
        "REMNAWAVE_SQUAD_ASSIGN_FAILED: all approaches failed user_id=%s squad=%s",
        user_id, squad_uuid[:8],
    )
    return False


async def get_user(user_ref: UserRef) -> Optional[Dict[str, Any]]:
    """GET /api/users/{user_id} — Remnawave 3.x numeric id lookup.

    Accepts int, numeric str, or (deprecated) legacy UUID string.  UUID
    strings return None with a warning — resolve the id first via
    `remnawave_id_resolver.get_remnawave_id_for(tg, kind)`.
    """
    user_id = normalize_user_id(user_ref)
    if user_id is None:
        return None
    return await _request("GET", f"/api/users/{user_id}")


async def get_user_by_short_uuid(short_uuid: str) -> Optional[Dict[str, Any]]:
    """GET /api/users/by-short-uuid/{shortUuid} — resolve entity by its
    shortUuid (still stable across the 2.x → 3.x jump).  Returns None on
    404 / any HTTP error.
    """
    if not short_uuid:
        return None
    from urllib.parse import quote
    path = f"/api/users/by-short-uuid/{quote(short_uuid, safe='')}"
    return await _request("GET", path, quiet=True)


async def get_users_stream_by_telegram_id(
    telegram_id: int, size: int = 10,
) -> Optional[List[Dict[str, Any]]]:
    """GET /api/users/stream?telegramId=X&size=N — 3.x replacement for
    the removed `/api/users/by-telegram-id/{id}` route.  Returns the list
    of matching user entities (may be empty) or None on failure.
    """
    if telegram_id is None:
        return None
    path = f"/api/users/stream?telegramId={int(telegram_id)}&size={int(size)}"
    result = await _request("GET", path, quiet=True)
    if result is None:
        return None
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        users = result.get("users")
        if isinstance(users, list):
            return users
        # Some panel builds still nest under "data"
        if isinstance(result.get("data"), list):
            return result["data"]
    return []


async def get_all_users(page_size: int = 1000, progress_cb=None) -> Optional[list]:
    """Fetch every Remnawave user via paginated GET /api/users.

    Remnawave caps `size` at 1000 (HTTP 400 above that). page_size=1000
    halves the page count vs the original 500 (~10 pages for the current
    10k-entity panel) without tripping the validation cap.

    Each page is retried up to 3 times with exponential backoff so a
    single transient failure (timeout / 5xx) doesn't trash a multi-page
    scan. Permanent failures (HTTP 4xx) propagate as None — callers must
    treat None as "cannot list" and fail loudly rather than act on
    partial data.

    If `progress_cb` is given, it's awaited after each page with
    (collected_count, total_or_none) so the caller can render a live
    counter.
    """
    import asyncio
    collected: list = []
    start = 0
    total: Optional[int] = None
    while True:
        page = None
        for attempt in range(3):
            page = await _request("GET", f"/api/users?size={page_size}&start={start}")
            if page is not None:
                break
            backoff = 1.5 ** attempt
            logger.warning(
                "REMNAWAVE_LIST: page start=%s attempt=%s failed, retrying in %.1fs",
                start, attempt + 1, backoff,
            )
            await asyncio.sleep(backoff)
        if page is None:
            logger.error("REMNAWAVE_LIST: page start=%s failed after 3 attempts", start)
            return None
        if isinstance(page, dict):
            batch = page.get("users") or []
            if page.get("total") is not None:
                total = page.get("total")
        elif isinstance(page, list):
            batch = page
        else:
            return None
        collected.extend(batch)
        if progress_cb is not None:
            try:
                if asyncio.iscoroutinefunction(progress_cb):
                    await progress_cb(len(collected), total)
                else:
                    progress_cb(len(collected), total)
            except Exception:
                pass
        if not batch or len(batch) < page_size:
            break
        if total is not None and len(collected) >= total:
            break
        start += page_size
        # Hard safety stop. Sized for the current prod base (~358k) with
        # plenty of headroom; raise if you grow past it.
        if start > 2_000_000:
            logger.error("REMNAWAVE_LIST: aborted, start exceeded 2_000_000")
            break
    return collected


_update_method: Optional[tuple] = None  # cached working (method, path_template)


async def update_user(user_ref: UserRef, **fields) -> Optional[Dict[str, Any]]:
    """Update user fields on Remnawave 3.x.

    3.x accepts PATCH /api/users/{user_id} with the changed fields.  We
    still auto-discover between the id-in-path (`{user_id}`) and the
    id-in-body (`/api/users/update`, `/api/users`) shapes for panel
    minor-version drift.
    """
    global _update_method
    user_id = normalize_user_id(user_ref)
    if user_id is None:
        return None

    body_with_id = {"userId": user_id, **fields}  # 3.x body key = userId (int)
    body_plain = dict(fields)

    # Use cached method if already discovered
    if _update_method:
        method, path_tpl = _update_method
        path = path_tpl.replace("{user_id}", str(user_id))
        payload = body_with_id if "user_id" not in path_tpl else body_plain
        return await _request(method, path, json=payload)

    # Probe all known Remnawave 3.x panel endpoint variants
    _variants = [
        ("PATCH", "/api/users/{user_id}", False),
        ("POST",  "/api/users/{user_id}", False),
        ("PATCH", "/api/users",           True),
        ("POST",  "/api/users/update",    True),
        ("PUT",   "/api/users",           True),
    ]
    for method, path_tpl, needs_id_in_body in _variants:
        path = path_tpl.replace("{user_id}", str(user_id))
        payload = body_with_id if needs_id_in_body else body_plain
        result = await _request(method, path, quiet=True, json=payload)
        if result is not None:
            _update_method = (method, path_tpl)
            logger.info(
                "REMNAWAVE_UPDATE_DISCOVERED: %s %s works, caching",
                method, path_tpl,
            )
            return result

    logger.error(
        "REMNAWAVE_UPDATE_FAIL: no endpoint worked for user_id=%s fields=%s. "
        "Tried: %s", user_id, list(fields.keys()),
        [(m, p) for m, p, _ in _variants],
    )
    return None


async def reset_user_traffic(user_ref: UserRef) -> Optional[Dict[str, Any]]:
    """POST /api/users/{user_id}/actions/reset-traffic (3.x path)."""
    user_id = normalize_user_id(user_ref)
    if user_id is None:
        return None
    # 3.x moved traffic actions under /actions/*; keep the flat path as
    # fallback for panels stuck on late-2.x.
    for path in (
        f"/api/users/{user_id}/actions/reset-traffic",
        f"/api/users/{user_id}/reset-traffic",
    ):
        result = await _request("POST", path, quiet=True)
        if result is not None:
            return result
    logger.error("REMNAWAVE_RESET_TRAFFIC_FAIL: user_id=%s", user_id)
    return None


# ── HWID devices ───────────────────────────────────────────────────────
#
# Panel 3.x routes (same shape as 2.x but the {userId} placeholder is now
# the numeric BigInt, not a UUID):
#   GET  /api/hwid/devices/{userId}            — list devices for user
#   POST /api/hwid/devices/delete              — body: {userId, hwid}
#   POST /api/hwid/devices/delete-all          — body: {userId}
#
# Device DTO fields: hwid, userId, platform, osVersion, deviceModel,
# userAgent, requestIp, createdAt, updatedAt. All optional except hwid.

async def get_user_hwid_devices(user_ref: UserRef) -> Optional[list]:
    """Return list of HWID device dicts for a user, or None on failure."""
    user_id = normalize_user_id(user_ref)
    if user_id is None:
        return None
    result = await _request("GET", f"/api/hwid/devices/{user_id}")
    if result is None:
        return None
    return result.get("devices") or []


async def delete_user_hwid_device(user_ref: UserRef, hwid: str) -> bool:
    """Revoke a single device by hwid. Returns True on success."""
    user_id = normalize_user_id(user_ref)
    if user_id is None:
        return False
    result = await _request(
        "POST", "/api/hwid/devices/delete",
        json={"userId": user_id, "hwid": hwid},
    )
    return result is not None


async def delete_all_user_hwid_devices(user_ref: UserRef) -> bool:
    """Revoke every device for a user. Returns True on success."""
    user_id = normalize_user_id(user_ref)
    if user_id is None:
        return False
    result = await _request(
        "POST", "/api/hwid/devices/delete-all",
        json={"userId": user_id},
    )
    return result is not None


async def delete_user(user_ref: UserRef) -> Optional[Dict[str, Any]]:
    """DELETE /api/users/{user_id} — panel returns 204 on success (handled
    upstream in _request, mapped to {"success": True})."""
    user_id = normalize_user_id(user_ref)
    if user_id is None:
        return None
    return await _request("DELETE", f"/api/users/{user_id}")


# ── Username search ────────────────────────────────────────────────────

async def find_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """Return the user entity whose `username` matches, or None if free.

    Panel 3.x preserves `GET /api/users/by-username/{username}`.  Returns
    None on any non-200/404 status so callers can decide whether to
    retry — the raw HTTP status is logged at WARN level for diagnostics.
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

async def get_user_traffic(user_ref: UserRef) -> Optional[Dict[str, Any]]:
    """Return traffic info including subscriptionUrl and happ_url, or None."""
    user = await get_user(user_ref)
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
