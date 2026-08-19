"""
Low-level HTTP client for Remnawave Panel API (v3.x, verified 3.3.0).

Все методы возвращают parsed JSON dict / list при успехе, None при
неудаче. Ошибки логируются, не бросаются — caller обязан проверить None.

⚠️ Совместимость: код рассчитан на Remnawave Panel 3.0.0+.
2.7.4 endpoints НЕ поддерживаются. Смотри docs/REMNAWAVE_3_MIGRATION.md.

Ключевые точки 3.x, отражённые здесь:
  - Все user-scoped path используют NUMERIC id (`{userId}`). UUID
    удалён из ответов панели полностью — использовать бесполезно.
    Наш кеш `remnawave_id` в БД заполнен pre-migration скриптом
    (см. scripts/prep_remnawave_v3_migration.py + migration 078).
  - POST /api/users и PATCH /api/users — без /create /update.
    PATCH идентифицирует юзера по `id` в body, не `uuid`.
  - Поиск: GET /api/users/stream с фильтрами (`telegramId`, `email`,
    `tag`, `username`) вместо удалённых `/by-*/{value}`. Курсорная
    пагинация: `nextCursor` (integer) → передавать в `cursor`.
  - DELETE — HTTP 204 No Content без тела. `_request` возвращает {}
    вместо None (иначе caller увидит "неудачу").
  - Bulk/async POST — HTTP 202 Accepted без тела, без `affectedRows`.
    Тоже возвращаем {}.
  - HWID: DELETE (не POST) на delete/delete-all; body поле `userId`.
  - Модуль IP-control переименован → /api/connections.
  - Actions user-scoped: /users/{userId}/actions/enable |disable
    |revoke |reset-traffic |extend.

Backwards-compat helpers:
  - resolve_user_id(username|id): один запрос к /users/stream, чтобы
    достать numeric id по username. Нужно на fallback-путях, когда в
    БД нет закешированного id (не забэкфильнутый юзер).
"""
import logging
from typing import Optional, Dict, Any, Union
from urllib.parse import quote

import httpx
import config

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {config.REMNAWAVE_API_TOKEN}",
        "Content-Type": "application/json",
    }


# 3.x возвращает 204 No Content для DELETE и 202 Accepted для bulk/async.
# Тела нет, но операция УСПЕШНА — не считать None (иначе caller решит "ошибка").
# Возвращаем пустой dict, чтобы `if result is None` не срабатывало.
_EMPTY_OK: Dict[str, Any] = {}


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

        # 204/202 → успех без тела. Возвращаем sentinel-{}, чтобы caller
        # различил успех vs неудачу.
        if resp.status_code in (202, 204):
            return dict(_EMPTY_OK)

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

        try:
            data = resp.json()
        except Exception:
            # 201/200 без JSON — трактуем как OK (редкий случай).
            return dict(_EMPTY_OK)
        # Remnawave обёртывает успешные ответы в {"response": {...}}.
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
    """POST /api/users — create a new Remnawave user (3.x).

    ⚠️ 3.x панель больше НЕ принимает custom `uuid` при создании — она
    генерит сама. Параметр `uuid` уходит в поле `vlessUuid` (VLESS UUID
    для connection strings, отдельный от panel-side id) — панель может
    его honour, читайте response.vlessUuid.

    Response содержит числовой `id` (новый идентификатор) и `vlessUuid`.
    Наш high-level код обязан сохранить `id` в remnawave_id колонке БД.
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

    path = "/api/users"
    if raw_response:
        return await _request_raw("POST", path, json=body)

    result = await _request("POST", path, json=body)
    if result:
        logger.info(
            "REMNAWAVE_CREATE: success for %s, id=%s squad_in_response=%s",
            username, result.get("id"), result.get("activeInternalSquads"),
        )
        if effective_squad:
            new_id = result.get("id")
            if new_id is not None and not (result.get("activeInternalSquads") or []):
                logger.warning(
                    "REMNAWAVE_SQUAD_NOT_IN_RESPONSE: user_id=%s, trying assign_user_to_squad",
                    new_id,
                )
                await assign_user_to_squad(new_id, effective_squad)
    else:
        logger.warning("REMNAWAVE_CREATE: failed for %s", username)
    return result


async def assign_user_to_squad(user_id: Union[str, int], squad_uuid: str) -> bool:
    """Assign existing user to a squad (3.x canonical endpoint).

    В 3.x единый путь: POST /api/internal-squads/{uuid}/bulk-actions/add-many-users
    body {userIds: [...]}. Fallback — PATCH /api/users с activeInternalSquads.
    """
    resolved = await _resolve_to_int_id(user_id)
    if resolved is None:
        logger.warning("assign_user_to_squad: cannot resolve id from %s", str(user_id)[:16])
        return False
    logger.info(
        "REMNAWAVE_SQUAD_ASSIGN_START: user_id=%s squad=%s",
        resolved, squad_uuid[:8],
    )

    # Approach 1: 3.x канонический endpoint (bulk на 1 юзере).
    result = await _request(
        "POST",
        f"/api/internal-squads/{squad_uuid}/bulk-actions/add-many-users",
        quiet=True,
        json={"userIds": [resolved]},
    )
    if result is not None:
        logger.info("REMNAWAVE_SQUAD_ASSIGN: via internal-squads bulk-actions user_id=%s", resolved)
        return True

    # Approach 2: PATCH /api/users body-based
    body = {"id": resolved, "activeInternalSquads": [squad_uuid]}
    r = await _request("PATCH", "/api/users", quiet=True, json=body)
    if r is not None:
        logger.info("REMNAWAVE_SQUAD_ASSIGN: via PATCH /users user_id=%s", resolved)
        return True

    logger.error(
        "REMNAWAVE_SQUAD_ASSIGN_FAILED: all approaches failed user_id=%s squad=%s",
        resolved, squad_uuid[:8],
    )
    return False


async def get_user(user_id: Union[str, int]) -> Optional[Dict[str, Any]]:
    """GET /api/users/{userId} — по числовому id (3.x).

    UUID в 3.x НЕ работает как path-параметр — панель отдаёт 400
    "expected number, received NaN". Мы получаем UUID из legacy-кеша
    (`subscriptions.remnawave_uuid`, миграция 048), поэтому здесь
    добавлен backwards-compat резолв: UUID → telegram_id через
    subscriptions-таблицу → id через find_user_by_telegram_id →
    GET /api/users/{id}.

    Это костыль на время фазы 2 (пока high-level модули не переписаны
    на remnawave_id-based кеш; см. миграцию 078 + prep-скрипт).
    Один лишний DB-select + один stream-запрос на UUID — не идеал по
    latency, но останавливает 400-спам в traffic_monitor.
    """
    s = str(user_id)
    if s.isdigit():
        return await _request("GET", f"/api/users/{s}")
    # UUID → резолв через нашу БД. Работает и для bypass (remnawave_uuid),
    # и для premium (remnawave_premium_uuid).
    tg_id = await _lookup_telegram_id_by_uuid(s)
    if tg_id is None:
        # UUID неизвестен нашей БД — legacy или удалённый. Пробуем
        # прямой запрос всё равно (панель отдаст 400, вернём None).
        return await _request("GET", f"/api/users/{s}", quiet=True)
    return await find_user_by_telegram_id(tg_id)


async def _lookup_telegram_id_by_uuid(uuid: str) -> Optional[int]:
    """Найти telegram_id в subscriptions по любому из UUID-полей.

    Совпадение может быть в remnawave_uuid (bypass) или
    remnawave_premium_uuid (premium). Fail-safe: ошибку БД глотаем,
    возвращаем None — caller упадёт в 404-путь.
    """
    try:
        import database
        pool = await database.get_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT telegram_id FROM subscriptions
                    WHERE remnawave_uuid = $1
                       OR remnawave_premium_uuid = $1
                    LIMIT 1""",
                uuid,
            )
        if row is None:
            return None
        return int(row["telegram_id"])
    except Exception:
        return None


async def _resolve_to_int_id(value: Union[str, int]) -> Optional[int]:
    """UUID / int / digit-str → numeric panel id (3.x).

    Универсальный резолвер для всех user-scoped endpoints, которым нужен
    integer id (delete/, actions/*, /users/{id}). Порядок:
      1. int / str-цифры → OK как есть.
      2. UUID → _lookup_telegram_id_by_uuid → find_user_by_telegram_id
         → берём .id из entity.
      3. None если не резолвится (caller должен обработать).
    """
    if isinstance(value, int):
        return value
    s = str(value)
    if s.isdigit():
        return int(s)
    tg_id = await _lookup_telegram_id_by_uuid(s)
    if tg_id is None:
        return None
    entity = await find_user_by_telegram_id(tg_id)
    if entity and entity.get("id") is not None:
        try:
            return int(entity["id"])
        except (TypeError, ValueError):
            pass
    return None


async def get_all_users(
    page_size: int = 250,
    progress_cb=None,
) -> Optional[list]:
    """GET /api/users/stream с курсорной пагинацией (3.x).

    3.x перевёл общий scan на stream-endpoint. Default size = 250,
    max = 1000. Пагинация через `nextCursor` (integer, был string в 2.x).

    Retries: 3 попытки на страницу с exponential backoff.

    progress_cb (опциональный, sync или async) вызывается после каждой
    страницы с (collected, total_or_none).
    """
    import asyncio
    if page_size > 1000:
        page_size = 1000
    collected: list = []
    cursor: Optional[int] = None
    total: Optional[int] = None
    safety_pages = 0
    while True:
        params = f"size={page_size}"
        if cursor is not None:
            params += f"&cursor={cursor}"
        page = None
        for attempt in range(3):
            page = await _request("GET", f"/api/users/stream?{params}")
            if page is not None:
                break
            backoff = 1.5 ** attempt
            logger.warning(
                "REMNAWAVE_STREAM: cursor=%s attempt=%s failed, retrying in %.1fs",
                cursor, attempt + 1, backoff,
            )
            await asyncio.sleep(backoff)
        if page is None:
            logger.error("REMNAWAVE_STREAM: cursor=%s failed after 3 attempts", cursor)
            return None
        if isinstance(page, dict):
            batch = page.get("users") or []
            if page.get("total") is not None:
                total = page.get("total")
            next_cursor = page.get("nextCursor")
        elif isinstance(page, list):
            batch = page
            next_cursor = None
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
        if not batch or next_cursor is None:
            break
        cursor = next_cursor
        safety_pages += 1
        if safety_pages > 8000:  # 8000 * 250 = 2M records safety
            logger.error("REMNAWAVE_STREAM: aborted at 8000 pages")
            break
    return collected


async def update_user(user_id: Union[str, int], **fields) -> Optional[Dict[str, Any]]:
    """PATCH /api/users — обновить поля юзера (3.x canonical).

    Body содержит `id` (integer). UUID резолвится через нашу БД →
    find_user_by_telegram_id → берём numeric id.
    """
    resolved = await _resolve_to_int_id(user_id)
    if resolved is None:
        logger.warning("update_user: cannot resolve id from %s", str(user_id)[:16])
        return None
    body = {"id": resolved, **fields}
    return await _request("PATCH", "/api/users", json=body)


async def reset_user_traffic(user_id: Union[str, int]) -> Optional[Dict[str, Any]]:
    """POST /api/users/{userId}/actions/reset-traffic (3.x)."""
    resolved = await _resolve_to_int_id(user_id)
    if resolved is None:
        return None
    return await _request("POST", f"/api/users/{resolved}/actions/reset-traffic")


async def enable_user(user_id: Union[str, int]) -> Optional[Dict[str, Any]]:
    """POST /api/users/{userId}/actions/enable (3.x dedicated)."""
    resolved = await _resolve_to_int_id(user_id)
    if resolved is None:
        return None
    return await _request("POST", f"/api/users/{resolved}/actions/enable")


async def disable_user(user_id: Union[str, int]) -> Optional[Dict[str, Any]]:
    """POST /api/users/{userId}/actions/disable (3.x dedicated)."""
    resolved = await _resolve_to_int_id(user_id)
    if resolved is None:
        return None
    return await _request("POST", f"/api/users/{resolved}/actions/disable")


async def revoke_user_subscription(user_id: Union[str, int]) -> Optional[Dict[str, Any]]:
    """POST /api/users/{userId}/actions/revoke — invalidate subscription URL."""
    resolved = await _resolve_to_int_id(user_id)
    if resolved is None:
        return None
    return await _request("POST", f"/api/users/{resolved}/actions/revoke")


async def extend_user_expiry(
    user_id: Union[str, int],
    *,
    days: Optional[int] = None,
    expire_at: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """POST /api/users/{userId}/actions/extend (3.x new).

    days      — прибавить дни к текущему expireAt (EXPIRED → +N от now
                и перевод в ACTIVE; ACTIVE → +N к дате истечения).
    expire_at — ISO-строка, полная замена expireAt.
    Один из параметров обязателен.
    """
    body: Dict[str, Any] = {}
    if days is not None:
        body["days"] = int(days)
    if expire_at is not None:
        body["expireAt"] = expire_at
    if not body:
        raise ValueError("extend_user_expiry: pass either days or expire_at")
    resolved = await _resolve_to_int_id(user_id)
    if resolved is None:
        return None
    return await _request("POST", f"/api/users/{resolved}/actions/extend", json=body)


# ── HWID devices (3.x) ─────────────────────────────────────────────────
#
#   GET    /api/hwid/devices/{userId}          — list devices
#   DELETE /api/hwid/devices/delete            — body: {userId, hwid}
#   DELETE /api/hwid/devices/delete-all        — body: {userId}
#
# ⚠️ HTTP verb POST → DELETE.

async def get_user_hwid_devices(user_id: Union[str, int]) -> Optional[list]:
    """Return list of HWID device dicts for a user, or None on failure."""
    resolved = await _resolve_to_int_id(user_id)
    if resolved is None:
        return None
    result = await _request("GET", f"/api/hwid/devices/{resolved}")
    if result is None:
        return None
    return result.get("devices") or []


async def delete_user_hwid_device(user_id: Union[str, int], hwid: str) -> bool:
    """Revoke a single device by hwid (3.x DELETE)."""
    resolved = await _resolve_to_int_id(user_id)
    if resolved is None:
        return False
    body = {"userId": resolved, "hwid": hwid}
    result = await _request("DELETE", "/api/hwid/devices/delete", json=body)
    return result is not None


async def delete_all_user_hwid_devices(user_id: Union[str, int]) -> bool:
    """Revoke every device for a user (3.x DELETE)."""
    resolved = await _resolve_to_int_id(user_id)
    if resolved is None:
        return False
    body = {"userId": resolved}
    result = await _request("DELETE", "/api/hwid/devices/delete-all", json=body)
    return result is not None


async def delete_user(user_id: Union[str, int]) -> Optional[Dict[str, Any]]:
    """DELETE /api/users/{userId} (3.x). Возвращает 204 → {}."""
    resolved = await _resolve_to_int_id(user_id)
    if resolved is None:
        return None
    return await _request("DELETE", f"/api/users/{resolved}")


# ── Search (3.x — только через stream) ────────────────────────────────
#
# В 3.x удалены /by-telegram-id, /by-email, /by-tag, /by-id. Замена —
# GET /api/users/stream с query-фильтрами.

async def _stream_first(query: str) -> Optional[Dict[str, Any]]:
    """Utility: /api/users/stream?query, вернуть первый user (уникальный поиск)."""
    result = await _request("GET", f"/api/users/stream?{query}")
    if result is None:
        return None
    if isinstance(result, dict):
        users = result.get("users") or []
        return users[0] if users else None
    if isinstance(result, list) and result:
        return result[0]
    return None


async def find_user_by_telegram_id(telegram_id: int) -> Optional[Dict[str, Any]]:
    """GET /api/users/stream?telegramId=X (3.x replacement for /by-telegram-id)."""
    if not telegram_id:
        return None
    return await _stream_first(f"telegramId={int(telegram_id)}")


async def find_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """POST /api/users/resolve body {username} — точечный поиск в 3.x.

    ⚠️ `/api/users/stream?username=X` НЕ работает: username не в списке
    stream-фильтров (только telegramId/email/tag/status/...). Панель
    молча игнорирует параметр и возвращает первую страницу — из-за
    этого _is_our_entity получал случайного юзера и падал в
    conflict_unrelated_user.

    Правильный 3.x-путь: POST /api/users/resolve с body { username } —
    единый resolver, принимающий { id | shortUuid | username | email
    | tag } и возвращающий одну entity.
    """
    if not username:
        return None
    result = await _request(
        "POST", "/api/users/resolve",
        quiet=True, json={"username": str(username)},
    )
    if result is None:
        return None
    # resolve может обернуть в {user: {...}} — распакуем.
    if isinstance(result, dict):
        if "user" in result and isinstance(result["user"], dict):
            return result["user"]
        if "id" in result or "username" in result:
            return result
    return None


async def find_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """GET /api/users/stream?email=X (3.x replacement for /by-email)."""
    if not email:
        return None
    return await _stream_first(f"email={quote(email, safe='')}")


async def find_user_by_short_uuid(short_uuid: str) -> Optional[Dict[str, Any]]:
    """POST /api/users/resolve body {shortUuid} (3.x — единый resolver)."""
    if not short_uuid:
        return None
    result = await _request(
        "POST", "/api/users/resolve",
        quiet=True, json={"shortUuid": str(short_uuid)},
    )
    if result is None:
        return None
    if isinstance(result, dict):
        if "user" in result and isinstance(result["user"], dict):
            return result["user"]
        if "id" in result:
            return result
    return None


# ── Convenience ───────────────────────────────────────────────────────

async def get_user_traffic(user_id: Union[str, int]) -> Optional[Dict[str, Any]]:
    """Return traffic info including subscriptionUrl and happ_url, or None."""
    user = await get_user(user_id)
    if not user:
        return None
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


async def resolve_user_id(username_or_tg: Union[str, int]) -> Optional[int]:
    """Резолвит numeric user.id по username или telegram_id (3.x).

    В 3.x — единственный fallback-путь для callers, у которых нет id в
    кеше (не забэкфильнутый юзер, впервые сталкиваемся). Порядок:
      1. Если уже integer / строка-цифра — возвращаем как есть.
      2. Пробуем как telegram_id (наш default username = str(tg_id)).
      3. Пробуем как username-строку.

    Возвращает None если не нашли — caller обязан обработать.
    """
    if not username_or_tg:
        return None
    if isinstance(username_or_tg, int):
        return int(username_or_tg)
    s = str(username_or_tg)
    if s.isdigit():
        # Может быть либо уже id панели, либо telegram_id (наш username).
        # Пробуем find_by_telegram_id — если наш → вернёт entity с .id.
        user = await find_user_by_telegram_id(int(s))
        if user and user.get("id") is not None:
            try:
                return int(user["id"])
            except (TypeError, ValueError):
                pass
        # Иначе — трактуем как уже numeric id панели.
        return int(s)
    # Нецифровая строка → username.
    user = await find_user_by_username(s)
    if user and user.get("id") is not None:
        try:
            return int(user["id"])
        except (TypeError, ValueError):
            pass
    return None
