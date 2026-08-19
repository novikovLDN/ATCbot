"""
Low-level HTTP client for Remnawave Panel API (v3.x).

Все методы возвращают parsed JSON dict при успехе, None при неудаче.
Ошибки логируются, но не бросаются — caller обязан проверить на None.

Совместимость: код рассчитан на Remnawave 3.x (проверено на 3.2.3).
2.7.4 endpoints НЕ поддерживаются — миграция описана в
docs/REMNAWAVE_3_MIGRATION.md.

Ключевые изменения 2.7.4 → 3.x (реализовано здесь):
  - path /api/users → /api/users/create, /users/get, /users/update
  - /by-username/ → /username/, /by-short-uuid/ → /short-uuid/
  - PATCH /users/update body-based (без auto-discover)
  - HWID delete: POST → DELETE, userUuid → userId в body
  - reset-traffic: /users/{id}/reset-traffic → /users/{id}/actions/reset-traffic
  - delete: DELETE /users/{id} → DELETE /users/delete/{id}
  - новые dedicated endpoints: enable, disable, revoke, extend

ID vs UUID: в 3.x path-параметр для user-actions — числовой userId.
Панель продолжает отдавать UUID в response.uuid (для совместимости с
subscription URLs), но /api/users/{path} ждёт integer. Наш кеш в БД
пока UUID-based — миграция 078 добавляет `remnawave_id` колонку.
Функции, требующие id, принимают integer ИЛИ строку, содержащую
только цифры.
"""
import logging
from typing import Optional, Dict, Any, Union

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
        # Remnawave wraps successful responses in {"response": {...}}.
        # В 3.x envelope сохранён.
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
        # Только warning — caller решает, критично или нет.
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
    external_squad_uuid: Optional[str] = None,
    raw_response: bool = False,
) -> Optional[Dict[str, Any]]:
    """POST /api/users/create — create a new Remnawave user (3.x).

    Extra keyword args (наследие с premium/samopis migration):
      uuid                 — VLESS UUID для форсинга; отправляется как
                             `vlessUuid` в body. Панель может честно
                             принять либо проигнорировать в зависимости
                             от version — читайте result['vlessUuid'].
      squad_uuid           — override config.REMNAWAVE_SQUAD_UUID
                             (например, MainServer для premium tier).
                             Пустая строка → пропускаем squad assignment.
      description          — passed through (например, "Imported from…").
      telegram_id          — `telegramId` для panel-side cross-reference.
                             КРИТИЧНО для _is_our_entity recovery в
                             remnawave_bypass._is_our_entity.
      traffic_limit_strategy — reset strategy (default NO_RESET).
      external_squad_uuid  — Task 6 override subscription Template.
      raw_response         — True → возвращаем _request_raw envelope,
                             False → распакованный dict (default).
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
        body["vlessUuid"] = uuid
    if description:
        body["description"] = description
    if telegram_id is not None:
        body["telegramId"] = int(telegram_id)
    if external_squad_uuid:
        body["externalSquadUuid"] = external_squad_uuid

    if squad_uuid is None:
        effective_squad = config.REMNAWAVE_SQUAD_UUID
    else:
        effective_squad = squad_uuid
    if effective_squad:
        body["activeInternalSquads"] = [effective_squad]

    path = "/api/users/create"
    if raw_response:
        return await _request_raw("POST", path, json=body)

    result = await _request("POST", path, json=body)
    if result:
        logger.info(
            "REMNAWAVE_CREATE: success for %s, response keys=%s squad_in_response=%s",
            username, list(result.keys()),
            result.get("activeInternalSquads"),
        )

        # Belt-and-suspenders: если squad не осел в response, дёргаем
        # dedicated endpoint. В 3.x чаще всего одного POST /create хватает.
        if effective_squad:
            user_uuid = result.get("uuid")
            if user_uuid and not (result.get("activeInternalSquads") or []):
                logger.warning(
                    "REMNAWAVE_SQUAD_NOT_IN_RESPONSE: user=%s, trying assign_user_to_squad",
                    user_uuid[:8],
                )
                await assign_user_to_squad(user_uuid, effective_squad)
    else:
        logger.warning("REMNAWAVE_CREATE: failed for %s", username)
    return result


async def assign_user_to_squad(user_uuid: str, squad_uuid: str) -> bool:
    """Assign existing user to a squad.

    В 3.x канонический endpoint — POST /api/squads/add-users-to-squad
    body {squadUuid, userUuids: [...]}. Оставляем ещё 2 fallback'а на
    случай минорных различий в патч-версиях (assign через PATCH-body в
    /users/update с activeInternalSquads).
    """
    logger.info(
        "REMNAWAVE_SQUAD_ASSIGN_START: user=%s squad=%s",
        user_uuid[:8], squad_uuid[:8],
    )

    # Approach 1: канонический
    result = await _request(
        "POST", "/api/squads/add-users-to-squad",
        quiet=True,
        json={"squadUuid": squad_uuid, "userUuids": [user_uuid]},
    )
    if result is not None:
        logger.info("REMNAWAVE_SQUAD_ASSIGN: via add-users-to-squad user=%s", user_uuid[:8])
        return True

    # Approach 2: PATCH user update с activeInternalSquads
    body = {"uuid": user_uuid, "activeInternalSquads": [squad_uuid]}
    r = await _request("PATCH", "/api/users/update", quiet=True, json=body)
    if r is not None:
        check = await get_user(user_uuid)
        if check and check.get("activeInternalSquads"):
            logger.info("REMNAWAVE_SQUAD_ASSIGN: via PATCH /users/update user=%s", user_uuid[:8])
            return True

    logger.error(
        "REMNAWAVE_SQUAD_ASSIGN_FAILED: all approaches failed user=%s squad=%s",
        user_uuid[:8], squad_uuid[:8],
    )
    return False


async def get_user(id_or_uuid: Union[str, int]) -> Optional[Dict[str, Any]]:
    """GET /api/users/{userId}.

    3.x path-параметр — integer userId. Панель может дополнительно
    поддерживать UUID для backwards-compat; наш вызов принимает оба,
    подставляем как есть. Если запрос 404 — caller обязан fallback'нуть
    на find_user_by_username / find_user_by_uuid.
    """
    return await _request("GET", f"/api/users/{id_or_uuid}")


async def get_all_users(page_size: int = 1000, progress_cb=None) -> Optional[list]:
    """GET /api/users/get?size=…&start=… — pagination scan.

    Remnawave капит `size` на 1000 (400 если выше). page_size=1000
    → ~10 страниц на 10k entity базе.

    Retries: 3 попытки на страницу с exponential backoff. При постоянной
    ошибке (4xx) возвращаем None — caller обязан считать «нельзя
    прочитать» и не действовать на partial data.

    progress_cb (опциональный, sync или async) вызывается после каждой
    страницы с (collected, total) для live-счётчика.
    """
    import asyncio
    collected: list = []
    start = 0
    total: Optional[int] = None
    while True:
        page = None
        for attempt in range(3):
            page = await _request("GET", f"/api/users/get?size={page_size}&start={start}")
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
        if start > 2_000_000:
            logger.error("REMNAWAVE_LIST: aborted, start exceeded 2_000_000")
            break
    return collected


async def update_user(id_or_uuid: Union[str, int], **fields) -> Optional[Dict[str, Any]]:
    """PATCH /api/users/update — обновить поля юзера (3.x canonical).

    В 3.x auto-discover больше не нужен: путь стабильный, body-based.
    Ключ идентификации в body — `uuid` (панель отдаёт его в response
    даже в 3.x). Если у вас в кеше числовой id — так и передавайте,
    панель распознает.
    """
    body = {"uuid": id_or_uuid, **fields}
    return await _request("PATCH", "/api/users/update", json=body)


async def _needs_int_id(value: Union[str, int]) -> Union[str, int]:
    """Резолвим UUID → int id для endpoints, которые требуют integer.

    Каноничные 3.x пути (delete/, actions/*) документированы как integer-only.
    Наш кеш пока UUID-based (миграция 078 добавит remnawave_id), поэтому
    делаем one-shot resolve через get_user → берём .id из response.
    Возвращаем int если удалось, иначе оригинал (пусть API отдаст 400/404
    и caller разберётся)."""
    if isinstance(value, int):
        return value
    s = str(value)
    if s.isdigit():
        return int(s)
    resolved = await resolve_user_id(s)
    return resolved if resolved is not None else s


async def reset_user_traffic(user_id_or_uuid: Union[str, int]) -> Optional[Dict[str, Any]]:
    """POST /api/users/{userId}/actions/reset-traffic (3.x)."""
    resolved = await _needs_int_id(user_id_or_uuid)
    return await _request("POST", f"/api/users/{resolved}/actions/reset-traffic")


async def enable_user(user_id_or_uuid: Union[str, int]) -> Optional[Dict[str, Any]]:
    """POST /api/users/{userId}/actions/enable (3.x dedicated).

    Заменяет update_user(status='ACTIVE') — предпочтительно, атомарно,
    без риска затереть другие поля.
    """
    resolved = await _needs_int_id(user_id_or_uuid)
    return await _request("POST", f"/api/users/{resolved}/actions/enable")


async def disable_user(user_id_or_uuid: Union[str, int]) -> Optional[Dict[str, Any]]:
    """POST /api/users/{userId}/actions/disable (3.x dedicated)."""
    resolved = await _needs_int_id(user_id_or_uuid)
    return await _request("POST", f"/api/users/{resolved}/actions/disable")


async def revoke_user_subscription(user_id_or_uuid: Union[str, int]) -> Optional[Dict[str, Any]]:
    """POST /api/users/{userId}/actions/revoke (3.x new) —
    инвалидирует subscription URL юзера. В 2.7.4 не было."""
    resolved = await _needs_int_id(user_id_or_uuid)
    return await _request("POST", f"/api/users/{resolved}/actions/revoke")


async def extend_user_expiry(
    user_id_or_uuid: Union[str, int],
    *,
    days: Optional[int] = None,
    expire_at: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """POST /api/users/{userId}/actions/extend (3.x new).

    Один из параметров ОБЯЗАТЕЛЕН:
      days       — сколько дней добавить к текущему expireAt.
      expire_at  — ISO-строка, полная замена expireAt.
    """
    body: Dict[str, Any] = {}
    if days is not None:
        body["days"] = int(days)
    if expire_at is not None:
        body["expireAt"] = expire_at
    if not body:
        raise ValueError("extend_user_expiry: pass either days or expire_at")
    resolved = await _needs_int_id(user_id_or_uuid)
    return await _request("POST", f"/api/users/{resolved}/actions/extend", json=body)


# ── HWID devices ───────────────────────────────────────────────────────
#
# 3.x: DELETE (не POST) на delete/delete-all endpoints. Body field
# переименован userUuid → userId, но по практике панель принимает оба
# (для backwards-compat), поэтому шлём оба поля.
#
# Routes:
#   GET    /api/hwid/devices/{userId}          — list devices
#   DELETE /api/hwid/devices/delete            — body: {userId, hwid}
#   DELETE /api/hwid/devices/delete-all        — body: {userId}

async def get_user_hwid_devices(user_id_or_uuid: Union[str, int]) -> Optional[list]:
    """Return list of HWID device dicts for a user, or None on failure."""
    result = await _request("GET", f"/api/hwid/devices/{user_id_or_uuid}")
    if result is None:
        return None
    return result.get("devices") or []


async def delete_user_hwid_device(user_id_or_uuid: Union[str, int], hwid: str) -> bool:
    """Revoke a single device by hwid (3.x DELETE)."""
    body = {"userId": user_id_or_uuid, "userUuid": user_id_or_uuid, "hwid": hwid}
    result = await _request("DELETE", "/api/hwid/devices/delete", json=body)
    return result is not None


async def delete_all_user_hwid_devices(user_id_or_uuid: Union[str, int]) -> bool:
    """Revoke every device for a user (3.x DELETE)."""
    body = {"userId": user_id_or_uuid, "userUuid": user_id_or_uuid}
    result = await _request("DELETE", "/api/hwid/devices/delete-all", json=body)
    return result is not None


async def delete_user(user_id_or_uuid: Union[str, int]) -> Optional[Dict[str, Any]]:
    """DELETE /api/users/delete/{userId} (3.x path)."""
    resolved = await _needs_int_id(user_id_or_uuid)
    return await _request("DELETE", f"/api/users/delete/{resolved}")


# ── Username / short-uuid search ───────────────────────────────────────

async def find_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """GET /api/users/username/{username} (3.x, без `by-` префикса).

    Возвращает user entity если username занят, None если свободен или
    при других ошибках (404 = свободен; иное — логируется).

    КРИТИЧНО для _is_our_entity в remnawave_bypass — recovery-путь
    при adopting existing entity (fix 62 для conflict_unrelated_user).
    """
    if not username:
        return None
    from urllib.parse import quote
    path = f"/api/users/username/{quote(username, safe='')}"
    raw = await _request_raw("GET", path)
    status = int(raw.get("status") or 0)
    if status == 200 and isinstance(raw.get("response"), dict):
        return raw["response"]
    if status == 404:
        # errorCode A063 = "no such user" — username свободен.
        return None
    logger.warning(
        "REMNAWAVE_FIND_UNEXPECTED_STATUS: username=%s status=%s body=%s",
        username, status, str(raw.get("body") or "")[:200],
    )
    return None


async def find_user_by_short_uuid(short_uuid: str) -> Optional[Dict[str, Any]]:
    """GET /api/users/short-uuid/{shortUuid} (3.x, без `by-` префикса)."""
    if not short_uuid:
        return None
    from urllib.parse import quote
    return await _request("GET", f"/api/users/short-uuid/{quote(short_uuid, safe='')}")


# ── Convenience ───────────────────────────────────────────────────────

async def get_user_traffic(id_or_uuid: Union[str, int]) -> Optional[Dict[str, Any]]:
    """Return traffic info including subscriptionUrl and happ_url, or None."""
    user = await get_user(id_or_uuid)
    if not user:
        return None
    # Traffic data может быть в userTraffic или на top level.
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


async def resolve_user_id(uuid_or_username: str) -> Optional[int]:
    """Резолвит числовой user.id по UUID или username.

    3.x actions endpoints (`/users/{id}/actions/*`) требуют integer id.
    В нашей БД пока кешируется UUID (миграция 078 добавит remnawave_id).
    Fallback путь: сначала пробуем GET /api/users/{uuid} — если панель
    отдаёт entity с полем `id` — берём. Иначе пробуем find_by_username.

    Возвращает None если не нашли — caller обязан обработать.
    """
    if not uuid_or_username:
        return None
    # 1. Пробуем как id прямо
    if isinstance(uuid_or_username, int) or (
        isinstance(uuid_or_username, str) and uuid_or_username.isdigit()
    ):
        return int(uuid_or_username)
    # 2. GET /api/users/{uuid} — панель может отдать entity с id
    user = await get_user(uuid_or_username)
    if user and user.get("id") is not None:
        try:
            return int(user["id"])
        except (TypeError, ValueError):
            pass
    # 3. find_by_username
    user = await find_user_by_username(uuid_or_username)
    if user and user.get("id") is not None:
        try:
            return int(user["id"])
        except (TypeError, ValueError):
            pass
    return None
