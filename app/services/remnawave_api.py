"""
Low-level HTTP client for Remnawave Panel API (verified against backend 3.4.3).

Все методы возвращают parsed JSON dict / list при успехе, None при
неудаче. Ошибки логируются, не бросаются — caller обязан проверить None.
Полная сверка с контрактом: docs/providers/remnawave_3.4.3.md.

Ключевые точки 3.4.3 (libs/contract), отражённые здесь:
  - Все user-scoped path используют NUMERIC id (`{userId}`). Поля `uuid`
    в ответе нет — есть `vlessUuid`; его бот и хранит в *_uuid колонках.
    Числовой id кешируется в remnawave_id / remnawave_premium_id.
  - POST /api/users (201) и PATCH /api/users (200) — без /create /update.
    PATCH идентифицирует юзера по `id` (или `username`) в body.
    PATCH: status только ACTIVE|DISABLED, expireAt только в будущем
    (иначе 400).
  - Поиск по username/shortUuid: POST /api/users/resolve → только
    {id, username, shortUuid}, полную entity дочитываем GET по id.
    (GET /api/users/by-username/{u} и /by-short-uuid/{s} в 3.4.3 тоже
    есть и отдают полную entity.)
  - GET /api/users/stream: фильтры telegramId/email/tag/status/
    trafficLimitStrategy/externalSquadUuid (username НЕТ). Курсор:
    `nextCursor` — string|null, передавать как есть в `cursor`; `hasMore`.
  - Дубль username на POST — HTTP 400 errorCode A019 (не 409),
    см. is_username_conflict.
  - DELETE — HTTP 204 No Content без тела. `_request` возвращает {}
    вместо None (иначе caller увидит "неудачу").
  - Bulk/async POST (add-many-users) — HTTP 202 Accepted. Тоже {}.
  - HWID: POST /api/hwid/devices/delete | delete-all, body `userId`.
  - Actions user-scoped: /users/{userId}/actions/enable |disable
    |revoke |reset-traffic |extend.
  - Троттлера в панели нет; вне dev панель требует X-Forwarded-For и
    X-Forwarded-Proto=https — бот шлёт их сам (_headers).
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Literal, Union

import httpx
import config

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


def _headers() -> dict:
    # 3.4.3 proxy-check middleware: outside dev, a request without
    # X-Forwarded-For AND X-Forwarded-Proto=https gets its socket closed.
    # Sent always — harmless behind the HTTPS proxy, required for a direct
    # backend URL (docs/providers/remnawave_3.4.3.md F12).
    return {
        "Authorization": f"Bearer {config.REMNAWAVE_API_TOKEN}",
        "Content-Type": "application/json",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-For": "127.0.0.1",
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


# Remnawave 3.4.3 answers a duplicate username on POST /api/users with
# HTTP 400 {"errorCode": "A019", "message": "User username already exists"}
# (libs/contract/constants/errors/errors.ts USER_USERNAME_ALREADY_EXISTS,
# src/common/exception/http-exception.filter.ts) — never 409. 409 is kept for
# a reverse proxy / older panel that might still send it.
_USERNAME_CONFLICT_CODE = "A019"


def is_username_conflict(raw: Optional[Dict[str, Any]]) -> bool:
    """True when a _request_raw envelope of POST /api/users means "this
    username is already taken" (a concurrent/interrupted run created it)."""
    status = int((raw or {}).get("status") or 0)
    if status == 409:
        return True
    if status != 400:
        return False
    body = (raw or {}).get("body")
    if isinstance(body, dict):
        if str(body.get("errorCode") or "") == _USERNAME_CONFLICT_CODE:
            return True
        return "username already exists" in str(body.get("message") or "").lower()
    return "username already exists" in str(body or "").lower()


# ── User tag (3.4.3) ───────────────────────────────────────────────────
#
# ONE tag per user: `tag` on POST /api/users and PATCH /api/users, string
# ^[A-Z0-9_]+$ up to 16 chars, nullable (create-user.command.ts:81-94,
# update-user.command.ts:41-50; UsersSchema.tag users.schema.ts:16). The bot
# sets the tariff tag on the premium entity and BYPASS on the bypass entity
# (tariffs.premium_panel_tag). A tag must never fail a purchase: an invalid
# value is dropped before sending, and a 400 that names the tag is answered by
# resending the same request once WITHOUT the tag (logged REMNAWAVE_TAG_REJECTED).

_TAG_RE = re.compile(r"^[A-Z0-9_]{1,16}$")
_TAG_WORD_RE = re.compile(r"\btag\b", re.IGNORECASE)


def clean_tag(tag: Any) -> Optional[str]:
    """`tag` if the panel accepts it (3.4.3 regex + length), else None (logged)."""
    if tag is None:
        return None
    if isinstance(tag, str) and _TAG_RE.match(tag):
        return tag
    logger.warning("REMNAWAVE_TAG_INVALID: %r not sent (^[A-Z0-9_]+$, max 16)", str(tag)[:40])
    return None


def _is_tag_rejection(raw: Optional[Dict[str, Any]]) -> bool:
    """A 400 whose body names the `tag` field (zod error path ["tag"] / message)."""
    if _raw_status(raw) != 400:
        return False
    return bool(_TAG_WORD_RE.search(str((raw or {}).get("body") or "")))


def _request_result(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A _request_raw envelope reduced to what _request returns."""
    if not raw.get("ok"):
        return None
    if _raw_status(raw) in (202, 204):
        return dict(_EMPTY_OK)
    resp = raw.get("response")
    return resp if isinstance(resp, (dict, list)) else dict(_EMPTY_OK)


async def _send_tagged(method: str, path: str, body: Dict[str, Any], *, raw: bool):
    """POST/PATCH `body`; if the panel rejects its tag, send it once more without
    the tag — the write itself (expireAt, limit, create) must still happen.
    Without a tag in the body this is exactly _request / _request_raw."""
    if not body.get("tag"):
        body = {k: v for k, v in body.items() if k != "tag"}
        if raw:
            return await _request_raw(method, path, json=body)
        return await _request(method, path, json=body)
    first = await _request_raw(method, path, json=body)
    if not first.get("ok") and _is_tag_rejection(first):
        logger.warning(
            "REMNAWAVE_TAG_REJECTED: %s %s tag=%s status=%s — sent again without the tag",
            method, path, body.get("tag"), _raw_status(first),
        )
        stripped = {k: v for k, v in body.items() if k != "tag"}
        if raw:
            return await _request_raw(method, path, json=stripped)
        return await _request(method, path, json=stripped)
    return first if raw else _request_result(first)


async def set_user_tag(user_id: int, tag: str) -> Dict[str, Any]:
    """Tag-only PATCH /api/users {id, tag} (the tag backfill). No fallback, no
    other field: returns the _request_raw envelope so the caller counts errors."""
    cleaned = clean_tag(tag)
    if cleaned is None:
        return {"ok": False, "status": 0, "body": None, "response": None, "error": "invalid_tag"}
    return await _request_raw("PATCH", "/api/users", json={"id": int(user_id), "tag": cleaned})


# ── Premium far-expireAt guard (defence in depth) ──────────────────────
#
# The premium entity (tg_{id}_premium) lives on the PAID date; only the bypass
# entity (username = str(telegram_id)) has a far-future expireAt (+10 y / 2099).
# 2026-09-14: bypass helpers resolved "the bypass" through contaminated bypass
# cache columns (holding the premium id/uuid) and PATCHed ~300 premium entities
# to +10 years. Every create/update carrying an expireAt more than
# PREMIUM_MAX_EXPIRE_AHEAD ahead is checked here: a premium target is refused
# (nothing sent, the caller sees its usual failure), logged
# REMNAWAVE_PREMIUM_FAR_EXPIRE_BLOCKED and alerted (admin_alerts cooldown).

PREMIUM_MAX_EXPIRE_AHEAD = timedelta(days=5 * 365)
_bg_tasks: set = set()


def _expire_beyond_premium_max(value: Any) -> bool:
    if value is None:
        return False
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt > datetime.now(timezone.utc) + PREMIUM_MAX_EXPIRE_AHEAD


def _is_premium_username(username: str, telegram_id: Any = None) -> bool:
    if not username:
        return False
    if username.endswith("_premium"):
        return True
    try:
        from app.services import remnawave_premium  # lazy: it imports this module
        return telegram_id is not None and username == remnawave_premium.build_premium_username(int(telegram_id))
    except Exception:
        return False


def _is_bypass_username(username: str, telegram_id: Any = None) -> bool:
    if username.isdigit():
        return True
    try:
        from app.services import remnawave_bypass  # lazy: it imports this module
        return telegram_id is not None and username == remnawave_bypass.build_bypass_username(int(telegram_id))
    except Exception:
        return False


def _alert_premium_far_expire(op: str, user_id: Any, username: str, expire_at: Any) -> None:
    """One admin alert per block (vpn_api cooldown + digest). Fire-and-forget:
    the refused write must not wait for Telegram."""
    async def _send() -> None:
        try:
            from app.services import admin_alerts, purchase_flow
            bot = purchase_flow._alert_bot()
            if bot is None:
                logger.error("REMNAWAVE_PREMIUM_FAR_EXPIRE_ALERT_NO_BOT: id=%s", user_id)
                return
            await admin_alerts.send_alert(bot, "vpn_api", "\n".join([
                "Premium far-future expireAt BLOCKED (nothing sent to the panel)",
                f"entity: id={user_id} username={username or '?'}",
                f"expireAt: {expire_at} (op: {op})",
                f"Premium must not get an expireAt more than {PREMIUM_MAX_EXPIRE_AHEAD.days} days ahead.",
                "Likely a bypass cache pointing at the premium entity. Logs: REMNAWAVE_PREMIUM_FAR_EXPIRE_BLOCKED.",
            ]))
        except Exception as e:  # noqa: BLE001
            logger.warning("REMNAWAVE_PREMIUM_FAR_EXPIRE_ALERT_FAILED: %s: %s", type(e).__name__, e)

    try:
        task = asyncio.get_running_loop().create_task(_send())
    except RuntimeError:
        return
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


async def _premium_far_expire_blocked(
    expire_at: Any,
    *,
    op: str,
    username: Optional[str] = None,
    telegram_id: Any = None,
    user_id: Optional[int] = None,
    trust_bypass: bool = False,
) -> bool:
    """True = refuse: `expire_at` is > now + PREMIUM_MAX_EXPIRE_AHEAD and the
    target is a premium entity. Known username decides; otherwise (update by
    id, only in this far-expireAt case) one GET reads it. A bypass username is
    never premium, even when its id sits in some remnawave_premium_id column;
    an unknown/legacy username falls back to _is_premium_entity (skipped for a
    caller that verified the bypass entity, _trust_bypass)."""
    if not _expire_beyond_premium_max(expire_at):
        return False
    uname = str(username or "").strip()
    premium = _is_premium_username(uname, telegram_id)
    if not premium and user_id is not None:
        ent = await _request("GET", f"/api/users/{int(user_id)}", quiet=True)
        ent = ent if isinstance(ent, dict) else {}
        uname = str(ent.get("username") or "").strip()
        if uname:
            premium = _is_premium_username(uname, ent.get("telegramId"))
            if not premium and not trust_bypass and not _is_bypass_username(uname, ent.get("telegramId")):
                premium = await _is_premium_entity(int(user_id))
        elif not trust_bypass:
            premium = await _is_premium_entity(int(user_id))
    if not premium:
        return False
    logger.error(
        "REMNAWAVE_PREMIUM_FAR_EXPIRE_BLOCKED: op=%s id=%s username=%r expireAt=%s — "
        "premium must not get an expireAt more than %d days ahead; nothing sent",
        op, user_id, uname or None, expire_at, PREMIUM_MAX_EXPIRE_AHEAD.days,
    )
    _alert_premium_far_expire(op, user_id, uname, expire_at)
    return True


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
    tag: Optional[str] = None,
    raw_response: bool = False,
) -> Optional[Dict[str, Any]]:
    """POST /api/users — create a new Remnawave user (3.x).

    `tag`: the user tag (clean_tag); a panel that rejects it gets the POST
    again without the tag — the user is still created (_send_tagged).

    ⚠️ 3.x панель больше НЕ принимает custom `uuid` при создании — она
    генерит сама. Параметр `uuid` уходит в поле `vlessUuid` (VLESS UUID
    для connection strings, отдельный от panel-side id) — панель может
    его honour, читайте response.vlessUuid.

    Response содержит числовой `id` (новый идентификатор) и `vlessUuid`.
    Наш high-level код обязан сохранить `id` в remnawave_id колонке БД.
    """
    # Единицы трафика 3.x: каноническое поле — trafficLimitBytes (bytes,
    # integer). Раньше шли три варианта (Bytes+Gb+Mb) в надежде что панель
    # выберет правильное — но 3.3 обрабатывает их непредсказуемо (иногда
    # Mb=15360 интерпретировалось как байты → лимит 15 KB → мгновенно
    # "трафик истёк"). Оставляем только Bytes — 3.3 схема принимает bytes
    # напрямую, никакой ambiguity.
    _bytes = int(traffic_limit_bytes)
    body: Dict[str, Any] = {
        "username": username,
        "shortUuid": short_uuid,
        "trafficLimitBytes": _bytes,
        "trafficLimitStrategy": traffic_limit_strategy,
        "status": "ACTIVE",
        "expireAt": expire_at,
        # 3.x: только hwidDeviceLimit (create-user.command.ts:105-112);
        # deviceLimit в контракте нет — ZodValidationPipe его вырезал.
        "hwidDeviceLimit": device_limit,
    }
    if uuid:
        body["vlessUuid"] = uuid
    if description:
        body["description"] = description
    if telegram_id is not None:
        body["telegramId"] = int(telegram_id)
    if external_squad_uuid:
        body["externalSquadUuid"] = external_squad_uuid
    cleaned_tag = clean_tag(tag)
    if cleaned_tag:
        body["tag"] = cleaned_tag

    if squad_uuid is None:
        effective_squad = config.REMNAWAVE_SQUAD_UUID
    else:
        effective_squad = squad_uuid
    if effective_squad:
        body["activeInternalSquads"] = [effective_squad]

    if await _premium_far_expire_blocked(expire_at, op="create", username=username, telegram_id=telegram_id):
        if raw_response:
            return {"ok": False, "status": 0, "body": None, "response": None,
                    "error": "premium_far_expire_blocked"}
        return None

    path = "/api/users"
    if raw_response:
        return await _send_tagged("POST", path, body, raw=True)

    result = await _send_tagged("POST", path, body, raw=False)
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
    "expected number, received NaN". Резолв UUID→numeric id:

      1) `_lookup_cached_id_by_uuid` — matched-column lookup из subscriptions
         (bypass_uuid → remnawave_id, premium_uuid → premium_id). Разделяет
         entities одного и того же tg_id, без коллизии в stream.
      2) resolve по shortUuid / vlessUuid → id.
      3) legacy fallback: если entity сохранена как username=str(tg_id) —
         `find_user_by_username(str(tg_id))`.

    ⚠️ Раньше здесь стоял `find_user_by_telegram_id` (stream ?telegramId=X),
    что при 2 entities одного юзера (bypass + premium) возвращало ПЕРВУЮ
    → GET bypass_uuid уходил в premium entity и наоборот. Fix: используем
    cached numeric id вместо stream.
    """
    s = str(user_id)
    if s.isdigit():
        return await _request("GET", f"/api/users/{s}")
    # UUID → numeric id (cached в БД, matched-column split).
    cached_id = await _lookup_cached_id_by_uuid(s)
    if cached_id is not None:
        return await _request("GET", f"/api/users/{cached_id}")
    # Fallback 1: shortUuid резолвится через /api/users/resolve.
    try:
        by_short = await find_user_by_short_uuid(s)
    except Exception:
        by_short = None
    if by_short and by_short.get("id") is not None:
        try:
            return await _request("GET", f"/api/users/{int(by_short['id'])}")
        except (TypeError, ValueError):
            pass
    # Fallback 2: legacy — прямой запрос (панель отдаст 400, вернём None).
    return await _request("GET", f"/api/users/{s}", quiet=True)


async def _lookup_telegram_id_by_uuid(uuid: str) -> Optional[int]:
    """Найти telegram_id в subscriptions по любому из UUID-полей."""
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


async def _lookup_cached_id_by_uuid(uuid: str) -> Optional[int]:
    """Ищем закешированный numeric id по UUID в subscriptions.

    ⚠️ КРИТИЧНО: возвращаем id ТОЙ ЖЕ колонки, что совпала по UUID —
    bypass_uuid → remnawave_id, premium_uuid → remnawave_premium_id.
    Раньше возвращали первый non-null → PATCH premium_uuid резолвился
    в bypass_id → PATCH шёл в bypass entity вместо premium.
    """
    try:
        import database
        pool = await database.get_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT
                     CASE
                       WHEN remnawave_uuid = $1 THEN remnawave_id
                       WHEN remnawave_premium_uuid = $1 THEN remnawave_premium_id
                     END AS matched_id
                   FROM subscriptions
                   WHERE remnawave_uuid = $1 OR remnawave_premium_uuid = $1
                   LIMIT 1""",
                uuid,
            )
        if row is None:
            return None
        val = row.get("matched_id")
        if val is None:
            return None
        try:
            return int(val)
        except (TypeError, ValueError):
            return None
    except Exception:
        return None


async def _resolve_to_int_id(value: Union[str, int]) -> Optional[int]:
    """UUID / int / digit-str → numeric panel id (3.x).

    Порядок (быстро → медленно):
      1. int / str-цифры → OK.
      2. UUID → cached remnawave_id/premium_id из subscriptions
         (1 DB SELECT, без обращения к панели — fast-path после backfill'а).
      3. UUID → find_user_by_telegram_id (DB + stream) — fallback для
         legacy без backfill'а.
      4. None если ничего не резолвится.
    """
    if isinstance(value, int):
        return value
    s = str(value)
    if s.isdigit():
        return int(s)
    cached = await _lookup_cached_id_by_uuid(s)
    if cached is not None:
        return cached
    tg_id = await _lookup_telegram_id_by_uuid(s)
    if tg_id is None:
        return None
    # Bypass and premium entities share telegramId, so stream?telegramId=
    # returns BOTH; taking the first one sent the PATCH to the other entity.
    # Pick the entity whose vlessUuid is the value we were given (the bot
    # stores vlessUuid — 3.4.3 UsersSchema has no `uuid`). A lone result is
    # still accepted (legacy rows cached a 2.x uuid that is no vlessUuid).
    page = await _request("GET", f"/api/users/stream?telegramId={int(tg_id)}")
    if isinstance(page, dict):
        users = page.get("users") or []
    elif isinstance(page, list):
        users = page
    else:
        users = []
    users = [u for u in users if isinstance(u, dict)]
    matched = [u for u in users if s in (str(u.get("vlessUuid") or ""), str(u.get("uuid") or ""))]
    if not matched and len(users) == 1:
        matched = users
    if len(matched) != 1:
        if users:
            logger.warning(
                "REMNAWAVE_RESOLVE_AMBIGUOUS: uuid=%s tg=%s entities=%d matched=%d — not guessing",
                s[:8], tg_id, len(users), len(matched),
            )
        return None
    try:
        return int(matched[0]["id"])
    except (KeyError, TypeError, ValueError):
        return None


async def get_all_users(
    page_size: int = 250,
    progress_cb=None,
    page_delay: float = 0.0,
    max_retries: int = 3,
) -> Optional[list]:
    """GET /api/users/stream с курсорной пагинацией (3.x).

    3.x перевёл общий scan на stream-endpoint. Default size = 250,
    max = 1000. Пагинация: ответ {users, nextCursor: string|null, hasMore}
    (get-users-stream.command.ts:50-58), `nextCursor` передаём как есть в
    `cursor`. Поля `total` в 3.4.3 нет — progress_cb получает total=None.

    Retries: `max_retries` попыток на страницу с exponential backoff.
    `page_delay` — пауза (сек) МЕЖДУ успешными страницами. Без неё стрим
    бёрстит и упирается в rate-limit панели (аборт после ретраев). Для
    больших/загруженных панелей передавай page_delay≈0.5-1.0 и max_retries≈6
    — «долго, но без упора в лимит».

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
        for attempt in range(max(1, max_retries)):
            page = await _request("GET", f"/api/users/stream?{params}")
            if page is not None:
                break
            # Длиннее ждём при устойчивом 429 — чтобы дождаться окна лимита,
            # а не аборт. cap ~30с.
            backoff = min(30.0, 1.7 ** attempt)
            logger.warning(
                "REMNAWAVE_STREAM: cursor=%s attempt=%s failed, retrying in %.1fs",
                cursor, attempt + 1, backoff,
            )
            await asyncio.sleep(backoff)
        if page is None:
            logger.error("REMNAWAVE_STREAM: cursor=%s failed after %s attempts", cursor, max_retries)
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
        # Пауза между страницами — чтобы не бёрстить в rate-limit панели.
        if page_delay > 0:
            await asyncio.sleep(page_delay)
    return collected


async def _is_premium_entity(numeric_id: int) -> bool:
    """True если numeric_id принадлежит PREMIUM entity (tg_*_premium в панели).

    Смотрим `subscriptions.remnawave_premium_id`. Cheap SELECT + очень
    важная defensive-проверка: premium должен ВСЕГДА оставаться
    trafficLimitBytes=0 (безлимит по ТЗ), любой PATCH с лимитом на
    premium — баг вышестоящего кода, дропаем.
    """
    try:
        import database
        pool = await database.get_pool()
        if pool is None:
            return False
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM subscriptions WHERE remnawave_premium_id = $1 LIMIT 1",
                int(numeric_id),
            )
        return row is not None
    except Exception:
        return False


async def update_user(user_id: Union[str, int], **fields) -> Optional[Dict[str, Any]]:
    """PATCH /api/users — обновить поля юзера (3.x canonical).

    Body содержит `id` (integer). UUID резолвится через нашу БД →
    find_user_by_telegram_id → берём numeric id.

    Единицы трафика: только `trafficLimitBytes` (bytes, integer) —
    каноническое поле 3.3.

    🔒 SAFETY-GUARD: premium entity ВСЕГДА безлимит (trafficLimitBytes=0
    по ТЗ). Если кто-то (баг в вышестоящем коде) шлёт trafficLimitBytes
    для premium → дропаем поле и логируем WARNING. Иначе получаем
    LIMITED-premium как жаловался клиент.
    """
    # Доверенный обход premium-guard: вызывающий (add_bypass_traffic) уже
    # авторитетно резолвнул bypass entity по username=str(tg) через
    # get_bypass_entity_safe и передаёт ЕЁ СОБСТВЕННЫЙ numeric id. В этом
    # случае _is_premium_entity — ложное срабатывание (id bypass-энтити мог
    # попасть в чью-то remnawave_premium_id из-за backfill-контаминации),
    # и SAFETY-DROP тихо гасит начисление → «оплатил, GB не пришли».
    trust_bypass = bool(fields.pop("_trust_bypass", False))

    resolved = await _resolve_to_int_id(user_id)
    if resolved is None:
        logger.warning("update_user: cannot resolve id from %s", str(user_id)[:16])
        return None
    # Явно чистим Gb/Mb-mirror если случайно передали — Bytes единственный
    # источник истины для лимита.
    fields.pop("trafficLimitGb", None)
    fields.pop("trafficLimitMb", None)
    # Premium никогда не должен получать trafficLimitBytes-лимит.
    if "trafficLimitBytes" in fields and not trust_bypass and await _is_premium_entity(resolved):
        logger.warning(
            "update_user: SAFETY-DROP trafficLimitBytes=%s for PREMIUM entity id=%s "
            "(premium должен быть безлимит по ТЗ, PATCH мимо-ушёл на premium вместо bypass) — "
            "возвращаем None, вышестоящий код обязан обработать как fail",
            fields.get("trafficLimitBytes"), resolved,
        )
        # Возвращаем None — сигнал "PATCH не отправлен, considered failure".
        # Иначе callers (add_traffic и т.п.) видят truthy dict и ложно
        # логируют SUCCESS, а трафик так и не добавлен.
        return None
    if "expireAt" in fields and await _premium_far_expire_blocked(
        fields["expireAt"], op="update", user_id=resolved, trust_bypass=trust_bypass,
    ):
        return None
    if "tag" in fields:
        # The bot only ever SETS a tag (never clears it): invalid / None → not sent.
        cleaned = clean_tag(fields.pop("tag"))
        if cleaned:
            fields["tag"] = cleaned
    body = {"id": resolved, **fields}
    if "tag" in body:
        return await _send_tagged("PATCH", "/api/users", body, raw=False)
    return await _request("PATCH", "/api/users", json=body)


async def revoke_user_subscription(user_id: Union[str, int]) -> Optional[Dict[str, Any]]:
    """POST /api/users/{userId}/actions/revoke — full «перевыпуск»: new shortUuid
    (subscription URL), vless uuid and passwords; the old links stop working.
    revokeOnlyPasswords=False keeps the URL change (RevokeUserSubscriptionBodyDto)."""
    resolved = await _resolve_to_int_id(user_id)
    if resolved is None:
        return None
    return await _request("POST", f"/api/users/{resolved}/actions/revoke",
                          json={"revokeOnlyPasswords": False})


# ── HWID devices (3.x) ─────────────────────────────────────────────────
#
#   GET    /api/hwid/devices/{userId}          — list devices
#   POST   /api/hwid/devices/delete            — body: {userId, hwid}
#   POST   /api/hwid/devices/delete-all        — body: {userId}
#
# ⚠️ Первичная миграция ставила verb DELETE (по докам) — панель
# отдавала 404 "Cannot DELETE /api/hwid/devices/delete". Живой контракт
# 3.x — POST (verb НЕ поменялся с 2.7.4, изменилось только поле
# userUuid → userId). Fallback на DELETE оставлен на случай, если
# некоторые панели всё-таки принимают DELETE.

async def get_user_hwid_devices(user_id: Union[str, int]) -> Optional[list]:
    """Return list of HWID device dicts for a user, or None on failure."""
    resolved = await _resolve_to_int_id(user_id)
    if resolved is None:
        return None
    result = await _request("GET", f"/api/hwid/devices/{resolved}")
    if result is None:
        return None
    return result.get("devices") or []


async def _hwid_delete(path_suffix: str, body: dict) -> bool:
    """POST-first, DELETE-fallback — реальный контракт 3.x."""
    path = f"/api/hwid/devices/{path_suffix}"
    # 1) POST (панель отвечает 200/201 в 3.x).
    raw = await _request_raw("POST", path, json=body)
    if raw and raw.get("ok"):
        return True
    # 405/404 на POST → верб не тот → пробуем DELETE.
    if int((raw or {}).get("status") or 0) in (404, 405):
        raw2 = await _request_raw("DELETE", path, json=body)
        if raw2 and raw2.get("ok"):
            return True
    return False


async def delete_user_hwid_device(user_id: Union[str, int], hwid: str) -> bool:
    """Revoke a single device by hwid (3.x POST, DELETE-fallback)."""
    resolved = await _resolve_to_int_id(user_id)
    if resolved is None:
        return False
    return await _hwid_delete("delete", {"userId": resolved, "hwid": hwid})


async def delete_all_user_hwid_devices(user_id: Union[str, int]) -> bool:
    """Revoke every device for a user (3.x POST, DELETE-fallback)."""
    resolved = await _resolve_to_int_id(user_id)
    if resolved is None:
        return False
    return await _hwid_delete("delete-all", {"userId": resolved})


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
    resolver принимает ровно одно из { id | shortUuid | username }
    (resolve-user.command.ts:18-34), 404 A025 если нет.

    ⚠️ /resolve возвращает ТОЛЬКО {id, username, shortUuid}
    (resolve-user.command.ts:36-42): без trafficLimitBytes, expireAt,
    subscriptionUrl. Дочитываем полную entity через GET /api/users/{id};
    если это не удалось — возвращаем None (урезанная entity давала
    limit=0 → add_bypass_traffic стирал накопленные ГБ, adopt — пустой URL).
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
    entity: Optional[Dict[str, Any]] = None
    if isinstance(result, dict):
        if "user" in result and isinstance(result["user"], dict):
            entity = result["user"]
        elif "id" in result or "username" in result:
            entity = result
    if entity is None:
        return None
    if all(k in entity for k in _STATE_REQUIRED_KEYS):
        return entity
    # 3.4.3 /resolve returns ONLY {id, username, shortUuid}
    # (commands/users/resolve-user.command.ts) → complete it via GET by id.
    full = None
    if entity.get("id") is not None:
        try:
            full = await _request(
                "GET", f"/api/users/{int(entity['id'])}", quiet=True,
            )
        except Exception as e:
            logger.warning(
                "find_user_by_username: full-fetch failed username=%s id=%s err=%s",
                username, entity.get("id"), e,
            )
            full = None
    if isinstance(full, dict):
        # Merge full over entity — полная entity обязана быть super-set.
        return {**entity, **full}
    # The trimmed entity has no trafficLimitBytes / expireAt / subscriptionUrl.
    # Returning it made callers read limit=0 (add_bypass_traffic then PATCHed
    # trafficLimitBytes=+N only, wiping the accumulated GB) or adopt with an
    # empty URL. A failed completion is a failed lookup.
    logger.warning(
        "REMNAWAVE_RESOLVE_INCOMPLETE: username=%s id=%s — full entity not "
        "fetched, lookup reported as failed",
        username, entity.get("id"),
    )
    return None


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

async def get_bypass_entity_safe(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Read bypass entity with self-heal on DB corruption.

    Проблема: legacy backfill писал в subscriptions.remnawave_id
    numeric id premium entity (не bypass) для тех юзеров, у кого
    stream по telegramId возвращал первым premium. Бот потом читал
    premium вместо bypass → "безлимит" вместо реальных ГБ.

    Fix: этот helper явно проверяет что resolved entity — bypass
    (`username == str(tg)`). Если mismatch — clear-cache, re-resolve
    через username, писать правильный id/uuid обратно в БД.

    Возвращает entity dict или None если нет.
    """
    import database
    expected_username = str(telegram_id)

    async def _looks_like_bypass(ent: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(ent, dict):
            return False
        return str(ent.get("username") or "").strip() == expected_username

    # 1) Быстрый путь — по кешу numeric id.
    try:
        bypass_id = await database.get_remnawave_id(telegram_id)
    except Exception:
        bypass_id = None
    if bypass_id is not None:
        try:
            ent = await get_user(int(bypass_id))
        except Exception:
            ent = None
        if await _looks_like_bypass(ent):
            # Сходимость колонок: remnawave_uuid должен указывать на ТУ ЖЕ
            # bypass-энтити, что и remnawave_id. Иначе add_bypass_traffic
            # (резолв по id) патчит одну энтити, а verify/подписка/агрегатор
            # (резолв по remnawave_uuid) читают другую → «GB не пришли».
            try:
                api_uuid = ent.get("uuid") or ent.get("vlessUuid")
                if api_uuid:
                    cached_uuid = await database.get_remnawave_uuid(telegram_id)
                    if str(cached_uuid or "") != str(api_uuid):
                        await database.set_remnawave_uuid(telegram_id, str(api_uuid))
                        logger.info(
                            "get_bypass_entity_safe: healed remnawave_uuid tg=%s "
                            "%s→%s (сходимость с remnawave_id)",
                            telegram_id, str(cached_uuid or "")[:8], str(api_uuid)[:8],
                        )
            except Exception:
                pass
            return ent
        # Mismatch — cached_id указывает на premium (или другого юзера).
        # Чистим bypass-id, ниже перерезолвим через username.
        try:
            logger.warning(
                "get_bypass_entity_safe: cached remnawave_id=%s для tg=%s "
                "указывает на entity username=%r (ожидалось %r) — "
                "clearing cache, self-heal via username",
                bypass_id, telegram_id,
                (ent or {}).get("username"), expected_username,
            )
        except Exception:
            pass

    # 2) Fallback — username resolve. Гарантированно bypass (или None).
    try:
        ent = await find_user_by_username(expected_username)
    except Exception:
        ent = None
    if not await _looks_like_bypass(ent):
        return None

    # 3) Self-heal: backfill correct id + uuid в БД.
    await _heal_bypass_cache(telegram_id, ent, why="username resolve")
    return ent


async def _heal_bypass_cache(telegram_id: int, ent: Optional[Dict[str, Any]], *, why: str) -> None:
    """Write `ent`'s id / uuid into subscriptions.remnawave_id / remnawave_uuid
    (the BYPASS columns) — only when its username is exactly str(telegram_id).
    Two short UPDATEs after the panel read, no transaction. Never raises."""
    import database
    if not isinstance(ent, dict) or str(ent.get("username") or "").strip() != str(telegram_id):
        return
    api_id = ent.get("id")
    api_uuid = ent.get("uuid") or ent.get("vlessUuid")
    try:
        if api_id is not None:
            await database.set_remnawave_id(telegram_id, int(api_id))
        if api_uuid:
            await database.set_remnawave_uuid(telegram_id, str(api_uuid))
    except Exception as e:
        logger.warning("REMNAWAVE_BYPASS_CACHE_HEAL_FAILED: tg=%s %s: %s", telegram_id, type(e).__name__, e)
        return
    logger.info(
        "REMNAWAVE_BYPASS_CACHE_HEALED: tg=%s id=%s uuid=%s (%s)",
        telegram_id, api_id, str(api_uuid or "")[:8], why,
    )


# ── Precise state readers (provisioning CAS, docs/audit/02 §B) ─────────
#
# get_bypass_entity_safe() returns None both for "no entity" and "panel down";
# the provisioning core must never mistake an outage for a missing entity
# (it would create instead of retrying). These readers classify explicitly:
#
#   ("present", entity)   2xx with an entity that passes the ownership check
#   ("absent", None)      404, or 4xx whose body says "not found"
#   ("unavailable", None) transport error/timeout (status 0), 5xx, 401, 403,
#                         408, 429, any other 4xx (ambiguous → retry, never
#                         create), an unexpected payload, REMNAWAVE disabled
#
# Lookup order = get_bypass_entity_safe: cached numeric id (GET /api/users/{id})
# → username resolve (POST /api/users/resolve). A cached-id 404/400 or an
# entity that fails the ownership check falls through to the username path;
# an "unavailable" cached-id read does NOT (no guessing while the panel is sick).
# get_premium_state never writes the DB cache. get_bypass_state writes it only
# after a cache mismatch resolved by username (_heal_bypass_cache: the entity's
# username is exactly str(tg)) — a contaminated remnawave_id / remnawave_uuid
# (premium id/uuid) must not keep sending the legacy paths to the premium.

StateKind = Literal["present", "absent", "unavailable"]

_STATE_UNAVAILABLE_STATUSES = frozenset({401, 403, 408, 429})
_STATE_REQUIRED_KEYS = ("id", "trafficLimitBytes", "expireAt", "subscriptionUrl")


def _raw_status(raw: Optional[Dict[str, Any]]) -> int:
    try:
        return int((raw or {}).get("status") or 0)
    except (TypeError, ValueError):
        return 0


def _raw_is_unavailable(raw: Optional[Dict[str, Any]]) -> bool:
    status = _raw_status(raw)
    return status == 0 or status >= 500 or status in _STATE_UNAVAILABLE_STATUSES


def _raw_is_not_found(raw: Optional[Dict[str, Any]]) -> bool:
    status = _raw_status(raw)
    if status == 404:
        return True
    return 400 <= status < 500 and "not found" in str((raw or {}).get("body") or "").lower()


def _raw_entity(raw: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    resp = (raw or {}).get("response")
    if isinstance(resp, dict):
        if isinstance(resp.get("user"), dict):
            return resp["user"]
        if "id" in resp or "username" in resp:
            return resp
    return None


async def _entity_state(
    *,
    username: str,
    cached_id: Optional[int],
    accept,
    what: str,
) -> "tuple[StateKind, Optional[Dict[str, Any]]]":
    if not config.REMNAWAVE_ENABLED:
        return "unavailable", None

    # 1) cached numeric id
    if cached_id is not None:
        raw = await _request_raw("GET", f"/api/users/{int(cached_id)}")
        if _raw_is_unavailable(raw):
            return "unavailable", None
        if raw.get("ok"):
            ent = _raw_entity(raw)
            if ent is not None and accept(ent):
                return "present", ent
            logger.warning(
                "REMNAWAVE_%s_STATE_CACHE_MISMATCH: cached id=%s username=%r (expected %r) — "
                "falling back to username resolve",
                what, cached_id, (ent or {}).get("username"), username,
            )

    # 2) username resolve
    raw = await _request_raw("POST", "/api/users/resolve", json={"username": username})
    if not raw.get("ok"):
        if _raw_is_unavailable(raw):
            return "unavailable", None
        if _raw_is_not_found(raw):
            return "absent", None
        return "unavailable", None
    ent = _raw_entity(raw)
    if ent is None or str(ent.get("username") or "").strip() != username:
        logger.warning(
            "REMNAWAVE_%s_STATE_UNEXPECTED: resolve(%r) returned username=%r",
            what, username, (ent or {}).get("username"),
        )
        return "unavailable", None
    if any(k not in ent for k in _STATE_REQUIRED_KEYS):
        # /resolve returns a trimmed user — complete it by numeric id.
        try:
            ent_id = int(ent["id"])
        except (KeyError, TypeError, ValueError):
            return "unavailable", None
        full = await _request_raw("GET", f"/api/users/{ent_id}")
        if not full.get("ok"):
            if _raw_is_unavailable(full):
                return "unavailable", None
            return ("absent", None) if _raw_is_not_found(full) else ("unavailable", None)
        full_ent = _raw_entity(full)
        if full_ent is None:
            return "unavailable", None
        ent = {**ent, **full_ent}
    return "present", ent


async def get_bypass_state(telegram_id: int) -> "tuple[StateKind, Optional[Dict[str, Any]]]":
    """Bypass entity (username == str(tg), same check as get_bypass_entity_safe)
    classified as present / absent / unavailable — see the block comment above."""
    import database
    try:
        cached_id = await database.get_remnawave_id(telegram_id)
    except Exception:
        cached_id = None
    expected = str(telegram_id)

    def _accept(ent: Dict[str, Any]) -> bool:
        return str(ent.get("username") or "").strip() == expected

    kind, ent = await _entity_state(username=expected, cached_id=cached_id, accept=_accept, what="BYPASS")
    if kind == "present" and cached_id is not None and isinstance(ent, dict):
        try:
            ent_id: Optional[int] = int(ent.get("id"))
        except (TypeError, ValueError):
            ent_id = None
        if ent_id is not None and ent_id != int(cached_id):
            await _heal_bypass_cache(telegram_id, ent, why="state reader cache mismatch")
    return kind, ent


async def get_premium_state(telegram_id: int) -> "tuple[StateKind, Optional[Dict[str, Any]]]":
    """Premium entity classified as present / absent / unavailable.

    Cached remnawave_premium_id is accepted if the entity has the premium
    username, or (legacy username) telegramId == tg and it is not the bypass
    entity (username == str(tg)). Username resolve uses build_premium_username
    — the same name create_premium_user_entity preflights/adopts by.
    """
    import database
    from app.services import remnawave_premium  # lazy: remnawave_premium imports this module
    try:
        cached_id = await database.get_remnawave_premium_id(telegram_id)
    except Exception:
        cached_id = None
    expected = remnawave_premium.build_premium_username(telegram_id)
    bypass_username = str(telegram_id)

    def _accept(ent: Dict[str, Any]) -> bool:
        uname = str(ent.get("username") or "").strip()
        if uname == expected:
            return True
        if uname == bypass_username:
            return False
        try:
            return int(ent.get("telegramId")) == int(telegram_id)
        except (TypeError, ValueError):
            return False

    return await _entity_state(username=expected, cached_id=cached_id, accept=_accept, what="PREMIUM")


async def get_bypass_traffic_safe(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Same as get_user_traffic, но гарантированно возвращает bypass
    (не premium). Self-heal DB кеш если поломан. Все bot-flow отображения
    трафика обхода должны использовать этот helper вместо
    get_user_traffic(remnawave_uuid).
    """
    entity = await get_bypass_entity_safe(telegram_id)
    if not isinstance(entity, dict):
        return None
    user_traffic = entity.get("userTraffic") or {}
    raw_sub_url = entity.get("subscriptionUrl", "") or ""
    try:
        from app.services.user_subscription_links import rewrite_sub_host
        sub_url = rewrite_sub_host(raw_sub_url) or raw_sub_url
    except Exception:
        sub_url = raw_sub_url
    return {
        "usedTrafficBytes": user_traffic.get("usedTrafficBytes", entity.get("usedTrafficBytes", 0)),
        "trafficLimitBytes": entity.get("trafficLimitBytes", 0),
        "deviceLimit": entity.get("hwidDeviceLimit", entity.get("deviceLimit", 0)),
        "onlineDevices": entity.get("onlineDevices", 0),
        "status": entity.get("status", "UNKNOWN"),
        "subscriptionUrl": sub_url,
        "happ_url": f"happ://add/{sub_url}" if sub_url else "",
    }


async def get_user_traffic(user_id: Union[str, int]) -> Optional[Dict[str, Any]]:
    """Return traffic info including subscriptionUrl and happ_url, or None.

    subscriptionUrl приводим через централизованный host-rewrite
    (sub.atlassecure.ru → subscription.vps-cloud.uk): cert для старого
    хоста невалиден → Happ/Incy покажут "сертификат недействителен",
    если отдать raw URL. Rewrite здесь = единая точка входа для всех
    callers (traffic, bypass_gift_setup, admin), не надо помнить о
    нём в каждом хендлере.
    """
    user = await get_user(user_id)
    if not user:
        return None
    user_traffic = user.get("userTraffic") or {}
    raw_sub_url = user.get("subscriptionUrl", "") or ""
    try:
        from app.services.user_subscription_links import rewrite_sub_host
        sub_url = rewrite_sub_host(raw_sub_url) or raw_sub_url
    except Exception:
        sub_url = raw_sub_url
    return {
        "usedTrafficBytes": user_traffic.get("usedTrafficBytes", user.get("usedTrafficBytes", 0)),
        "trafficLimitBytes": user.get("trafficLimitBytes", 0),
        # 3.x: hwidDeviceLimit — новое имя, оставляем fallback на старое.
        "deviceLimit": user.get("hwidDeviceLimit", user.get("deviceLimit", 0)),
        "onlineDevices": user.get("onlineDevices", 0),
        "status": user.get("status", "UNKNOWN"),
        "subscriptionUrl": sub_url,
        "happ_url": f"happ://add/{sub_url}" if sub_url else "",
    }


# ── Read-only panel statistics (dashboard) ────────────────────────────
#
# Contract: remnawave/backend tag 3.4.3, libs/contract. GET only — these
# helpers never mutate the panel. Short timeout: the dashboard must not
# hang on a slow panel, it shows "panel unavailable" instead. Callers
# cache the results (app/services/panel_stats.py); never call these in a
# loop per user and never while holding a DB connection.

_READ_TIMEOUT = httpx.Timeout(connect=3.0, read=6.0, write=3.0, pool=3.0)


async def _get_readonly(path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Any]:
    return await _request("GET", path, params=params, timeout=_READ_TIMEOUT)


async def get_system_stats(tz: str = "Europe/Moscow") -> Optional[Dict[str, Any]]:
    """GET /api/system/stats (commands/system/get-stats.command.ts).

    users.statusCounts {ACTIVE, DISABLED, LIMITED, EXPIRED}, users.totalUsers,
    onlineStats {lastDay, lastWeek, neverOnline, onlineNow},
    nodes {totalOnline, totalBytesLifetime (string of bytes)}, cpu, memory, uptime.
    """
    return await _get_readonly("/api/system/stats", {"tz": tz})


async def get_bandwidth_stats(tz: str = "Europe/Moscow") -> Optional[Dict[str, Any]]:
    """GET /api/system/stats/bandwidth (get-bandwidth-stats.command.ts).

    bandwidthLastTwoDays / LastSevenDays / Last30Days / CalendarMonth /
    CurrentYear, each {current, previous, difference} as IEC strings ("1.2 TiB").
    """
    return await _get_readonly("/api/system/stats/bandwidth", {"tz": tz})


async def get_nodes() -> Optional[list]:
    """GET /api/nodes (nodes/get-nodes.command.ts, models/nodes.schema.ts)."""
    return await _get_readonly("/api/nodes")


async def get_nodes_metrics() -> Optional[Dict[str, Any]]:
    """GET /api/system/nodes/metrics (get-nodes-metrics.command.ts):
    {nodes: [{nodeUuid, nodeName, countryEmoji, providerName, usersOnline,
    inboundsStats, outboundsStats}]}."""
    return await _get_readonly("/api/system/nodes/metrics")


async def get_nodes_usage(start: str, end: str, top_nodes: int = 20) -> Optional[Dict[str, Any]]:
    """GET /api/bandwidth-stats/nodes?start&end&topNodesLimit
    (bandwidth-stats/nodes/get-stats-nodes-usage.command.ts). Dates are
    YYYY-MM-DD. Returns {categories, sparklineData, topNodes, series}."""
    return await _get_readonly(
        "/api/bandwidth-stats/nodes",
        {"start": start, "end": end, "topNodesLimit": int(top_nodes)},
    )


async def get_hwid_stats() -> Optional[Dict[str, Any]]:
    """GET /api/hwid/devices/stats (hwid/get-hwid-devices-stats.command.ts):
    {byPlatform: [{platform, count, byApp}], stats: {totalUniqueDevices,
    totalHwidDevices, averageHwidDevicesPerUser}}."""
    return await _get_readonly("/api/hwid/devices/stats")
