"""FakeRemnawaveHTTP — the Remnawave 3.4.3 REST API as an httpx MockTransport.

Unlike tests/fakes/panel.FakePanel (which replaces the Python functions the
provisioning core calls), this fake sits BELOW `app.services.remnawave_api`:
every code path — legacy purchase_flow / remnawave_service / remnawave_bypass
/ remnawave_premium AND the outbox — goes through the real HTTP client code
(_request / _request_raw, id resolution, envelope unwrap) and ends up here.

Contract (docs/providers/remnawave_3.4.3.md, libs/contract):
  POST   /api/users                     201 {response: user}; duplicate username →
                                        400 {errorCode: A019}
  PATCH  /api/users                     body {id | username, ...}; 404 unknown;
                                        400 status ∉ {ACTIVE, DISABLED}, expireAt
                                        not in the future, trafficLimitBytes < 0
  GET    /api/users/{id}                numeric id only (else 400 "expected number")
  POST   /api/users/resolve             {id|username|shortUuid} → {id, username, shortUuid}
  GET    /api/users/stream              ?telegramId=&size=&cursor= → {users, nextCursor, hasMore}
  POST   /api/users/{id}/actions/revoke new shortUuid
  DELETE /api/users/{id}                204
  POST   /api/internal-squads/{sq}/bulk-actions/add-many-users   202
  GET    /api/hwid/devices/{id}         {devices: [], total: 0}
  POST   /api/hwid/devices/delete[-all] 200
Anything else → 404.

User tag (create-user.command.ts:81-94, update-user.command.ts:41-50): `tag` on
POST / PATCH, ^[A-Z0-9_]+$ max 16 chars, nullable on PATCH (null clears); anything
else → 400 zod error with path ["tag"]. Stored on the user and returned in every
view (UsersSchema.tag); GET /api/users/stream?tag= filters by it.

Failure injection:
  panel.down = True                    transport error on every request
  panel.fail(method, pred, status, times)  answer `status` to matching requests
  panel.ignore_patches = True          PATCH answers 200 with the entity, applies nothing
  panel.reject_tags = True             POST / PATCH carrying a non-null tag → 400 naming
                                       the tag (a panel that does not accept tags)
"""
from __future__ import annotations

import itertools
import json
import re
import uuid as uuid_lib
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import httpx

GIB = 1024 ** 3
_TAG_RE = re.compile(r"^[A-Z0-9_]+$")
_NUM_PATH = re.compile(r"^/api/users/(?P<id>[^/]+)$")
_REVOKE_PATH = re.compile(r"^/api/users/(?P<id>\d+)/actions/revoke$")
_SQUAD_PATH = re.compile(r"^/api/internal-squads/(?P<sq>[^/]+)/bulk-actions/add-many-users$")
_HWID_LIST = re.compile(r"^/api/hwid/devices/(?P<id>\d+)$")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _json(status: int, payload: Any = None) -> httpx.Response:
    if payload is None:
        return httpx.Response(status)
    return httpx.Response(status, json=payload)


def _not_found() -> httpx.Response:
    return _json(404, {"message": "User not found", "errorCode": "A063", "statusCode": 404})


@dataclass
class _FailRule:
    method: str
    pred: Callable[[httpx.Request, Dict[str, Any]], bool]
    status: int
    times: int


class FakeRemnawaveHTTP:
    BASE = "https://panel.test"

    def __init__(self) -> None:
        self.users: Dict[int, Dict[str, Any]] = {}
        self.requests: List[tuple] = []           # (method, path, json-body)
        self.down = False
        self.ignore_patches = False
        self.reject_tags = False
        self._rules: List[_FailRule] = []
        self._ids = itertools.count(1)

    # ── failure injection ────────────────────────────────────────────
    def fail(self, method: str, pred: Callable[[httpx.Request, Dict[str, Any]], bool] = lambda r, b: True,
             *, status: int = 500, times: int = 1_000_000) -> None:
        self._rules.append(_FailRule(method.upper(), pred, status, times))

    def clear_failures(self) -> None:
        self._rules.clear()
        self.down = False
        self.ignore_patches = False
        self.reject_tags = False

    # ── state helpers ────────────────────────────────────────────────
    def by_username(self, username: str) -> Optional[Dict[str, Any]]:
        for u in self.users.values():
            if u["username"] == username:
                return u
        return None

    def premium(self, tg: int) -> Optional[Dict[str, Any]]:
        return self.by_username(f"tg_{tg}_premium")

    def bypass(self, tg: int) -> Optional[Dict[str, Any]]:
        return self.by_username(str(tg))

    def premium_expire(self, tg: int) -> Optional[datetime]:
        ent = self.premium(tg)
        return ent["expireAt"] if ent else None

    def bypass_limit(self, tg: int) -> Optional[int]:
        ent = self.bypass(tg)
        return ent["trafficLimitBytes"] if ent else None

    def seed(self, username: str, *, tg: Optional[int], limit: int, expire_at: datetime,
             status: str = "ACTIVE", tag: Optional[str] = None) -> Dict[str, Any]:
        ent = self._new(username=username, tg=tg, limit=limit, expire_at=expire_at, status=status, tag=tag)
        return ent

    def seed_premium(self, tg: int, expire_at: datetime, *, tag: Optional[str] = None) -> Dict[str, Any]:
        return self.seed(f"tg_{tg}_premium", tg=tg, limit=0, expire_at=expire_at, tag=tag)

    def seed_bypass(self, tg: int, limit: int, *, tag: Optional[str] = None) -> Dict[str, Any]:
        return self.seed(str(tg), tg=tg, limit=limit,
                         expire_at=datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc), tag=tag)

    def tag_patches(self) -> List[tuple]:
        """PATCH /api/users requests that carried a `tag`."""
        return [r for r in self.requests if r[0] == "PATCH" and r[1] == "/api/users"
                and isinstance(r[2], dict) and "tag" in r[2]]

    def _tag_error(self, b: Dict[str, Any], *, nullable: bool) -> Optional[httpx.Response]:
        """3.4.3 zod validation of `tag` (None = accepted)."""
        if "tag" not in b:
            return None
        tag = b["tag"]
        if tag is None:
            return None if nullable else _json(400, {"statusCode": 400, "message": "Validation failed",
                                                     "errors": [{"path": ["tag"], "message": "Expected string"}]})
        if self.reject_tags or not (isinstance(tag, str) and _TAG_RE.match(tag) and len(tag) <= 16):
            return _json(400, {"statusCode": 400, "message": "Validation failed", "errors": [{
                "validation": "regex", "code": "invalid_string", "path": ["tag"],
                "message": "Tag can only contain uppercase letters, numbers, underscores"}]})
        return None

    def writes(self) -> List[tuple]:
        return [r for r in self.requests if r[0] in ("POST", "PATCH", "DELETE")
                and not r[1].startswith("/api/users/resolve")]

    def _new(self, *, username: str, tg: Optional[int], limit: int, expire_at: datetime,
             status: str = "ACTIVE", vless: Optional[str] = None, short: Optional[str] = None,
             squads: Optional[list] = None, device_limit: Optional[int] = None,
             description: Optional[str] = None, external_squad: Optional[str] = None,
             tag: Optional[str] = None) -> Dict[str, Any]:
        uid = next(self._ids)
        short = short or uuid_lib.uuid4().hex[:16]
        ent = {
            "id": uid,
            "vlessUuid": vless or str(uuid_lib.uuid4()),
            "shortUuid": short,
            "username": username,
            "telegramId": tg,
            "status": status,
            "trafficLimitBytes": int(limit),
            "trafficLimitStrategy": "NO_RESET",
            "expireAt": parse_dt(expire_at),
            "hwidDeviceLimit": device_limit,
            "description": description,
            "tag": tag,
            "externalSquadUuid": external_squad,
            "activeInternalSquads": [{"uuid": s, "name": "Clients"} for s in (squads or [])],
            "subscriptionUrl": f"{self.BASE}/api/sub/{short}",
            "userTraffic": {"usedTrafficBytes": 0, "lifetimeUsedTrafficBytes": 0, "onlineAt": None,
                            "firstConnectedAt": None, "lastConnectedNodeUuid": None},
            "createdAt": _iso(datetime.now(timezone.utc)),
        }
        self.users[uid] = ent
        return ent

    @staticmethod
    def view(ent: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(ent)
        out["expireAt"] = _iso(ent["expireAt"])
        out["usedTrafficBytes"] = ent["userTraffic"]["usedTrafficBytes"]
        return out

    # ── transport ────────────────────────────────────────────────────
    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        try:
            body = json.loads(request.content.decode() or "null") if request.content else None
        except ValueError:
            body = None
        self.requests.append((request.method, path + (f"?{request.url.query.decode()}" if request.url.query else ""), body))
        if self.down:
            raise httpx.ConnectError("FakeRemnawaveHTTP: panel down", request=request)
        for rule in self._rules:
            if rule.times > 0 and rule.method == request.method and rule.pred(request, body or {}):
                rule.times -= 1
                return _json(rule.status, {"message": "injected failure", "statusCode": rule.status})
        m = request.method
        if path == "/api/users" and m == "POST":
            return self._create(body or {})
        if path == "/api/users" and m == "PATCH":
            return self._patch(body or {})
        if path == "/api/users/resolve" and m == "POST":
            return self._resolve(body or {})
        if path == "/api/users/stream" and m == "GET":
            tg = request.url.params.get("telegramId")
            tag = request.url.params.get("tag")
            users = [self.view(u) for u in self.users.values()
                     if (tg is None or str(u.get("telegramId")) == str(tg))
                     and (tag is None or u.get("tag") == tag)]
            return _json(200, {"response": {"users": users, "nextCursor": None, "hasMore": False}})
        mm = _SQUAD_PATH.match(path)
        if mm and m == "POST":
            for i in (body or {}).get("userIds") or []:
                ent = self.users.get(int(i))
                if ent is not None:
                    ent["activeInternalSquads"] = [{"uuid": mm["sq"], "name": "Clients"}]
            return _json(202)
        mm = _REVOKE_PATH.match(path)
        if mm and m == "POST":
            ent = self.users.get(int(mm["id"]))
            if ent is None:
                return _not_found()
            ent["shortUuid"] = uuid_lib.uuid4().hex[:16]
            ent["subscriptionUrl"] = f"{self.BASE}/api/sub/{ent['shortUuid']}"
            return _json(200, {"response": self.view(ent)})
        mm = _HWID_LIST.match(path)
        if mm and m == "GET":
            return _json(200, {"response": {"devices": [], "total": 0}})
        if path in ("/api/hwid/devices/delete", "/api/hwid/devices/delete-all") and m == "POST":
            return _json(200, {"response": {"devices": [], "total": 0}})
        mm = _NUM_PATH.match(path)
        if mm:
            raw_id = mm["id"]
            if not raw_id.isdigit():
                return _json(400, {"message": "Validation failed: expected number, received NaN"})
            ent = self.users.get(int(raw_id))
            if m == "GET":
                return _json(200, {"response": self.view(ent)}) if ent else _not_found()
            if m == "DELETE":
                if ent is None:
                    return _not_found()
                del self.users[int(raw_id)]
                return _json(204)
        return _json(404, {"message": f"Cannot {m} {path}"})

    def _create(self, b: Dict[str, Any]) -> httpx.Response:
        username = str(b.get("username") or "")
        if not username:
            return _json(400, {"message": "username required"})
        if self.by_username(username) is not None:
            return _json(400, {"errorCode": "A019", "message": "User username already exists"})
        if b.get("status") not in (None, "ACTIVE", "DISABLED"):
            return _json(400, {"message": "invalid status"})
        limit = int(b.get("trafficLimitBytes") or 0)
        if limit < 0:
            return _json(400, {"message": "trafficLimitBytes must be >= 0"})
        tag_err = self._tag_error(b, nullable=True)
        if tag_err is not None:
            return tag_err
        ent = self._new(
            username=username, tg=b.get("telegramId"), limit=limit,
            expire_at=parse_dt(b["expireAt"]), status=b.get("status") or "ACTIVE",
            vless=b.get("vlessUuid"), short=b.get("shortUuid"), squads=b.get("activeInternalSquads"),
            device_limit=b.get("hwidDeviceLimit"), description=b.get("description"),
            external_squad=b.get("externalSquadUuid"), tag=b.get("tag"),
        )
        return _json(201, {"response": self.view(ent)})

    def _patch(self, b: Dict[str, Any]) -> httpx.Response:
        ent = None
        if b.get("id") is not None:
            ent = self.users.get(int(b["id"]))
        elif b.get("username"):
            ent = self.by_username(str(b["username"]))
        if ent is None:
            return _not_found()
        fields = {k: v for k, v in b.items() if k not in ("id", "username")}
        status = fields.get("status")
        if status is not None and status not in ("ACTIVE", "DISABLED"):
            return _json(400, {"message": "status must be ACTIVE or DISABLED"})
        if "expireAt" in fields and parse_dt(fields["expireAt"]) <= datetime.now(timezone.utc):
            return _json(400, {"message": "expireAt must be in the future"})
        if fields.get("trafficLimitBytes") is not None and int(fields["trafficLimitBytes"]) < 0:
            return _json(400, {"message": "trafficLimitBytes must be >= 0"})
        tag_err = self._tag_error(fields, nullable=True)
        if tag_err is not None:
            return tag_err
        if self.ignore_patches:
            return _json(200, {"response": self.view(ent)})
        for k, v in fields.items():
            if k == "expireAt":
                ent[k] = parse_dt(v)
            elif k == "activeInternalSquads":
                ent[k] = [{"uuid": s, "name": "Clients"} if isinstance(s, str) else s for s in (v or [])]
            else:
                ent[k] = v
        return _json(200, {"response": self.view(ent)})

    def _resolve(self, b: Dict[str, Any]) -> httpx.Response:
        keys = [k for k in ("id", "username", "shortUuid") if b.get(k) is not None]
        if len(keys) != 1:
            return _json(400, {"message": "exactly one of id/username/shortUuid"})
        k = keys[0]
        ent = None
        for u in self.users.values():
            if str(u.get(k)) == str(b[k]):
                ent = u
                break
        if ent is None:
            return _json(404, {"message": "User not found", "errorCode": "A025"})
        return _json(200, {"response": {"id": ent["id"], "username": ent["username"],
                                        "shortUuid": ent["shortUuid"]}})

    # ── wiring ───────────────────────────────────────────────────────
    def httpx_shim(self) -> SimpleNamespace:
        """Stand-in for the `httpx` module inside app.services.remnawave_api:
        same Timeout / exceptions, AsyncClient bound to this transport."""
        transport = httpx.MockTransport(self.handler)

        def client(*args, **kwargs):
            kwargs.pop("transport", None)
            return httpx.AsyncClient(*args, transport=transport, **kwargs)

        return SimpleNamespace(
            AsyncClient=client, Timeout=httpx.Timeout, TimeoutException=httpx.TimeoutException,
            HTTPError=httpx.HTTPError, HTTPStatusError=httpx.HTTPStatusError,
            ConnectError=httpx.ConnectError, RequestError=httpx.RequestError,
        )

    def install(self, monkeypatch) -> "FakeRemnawaveHTTP":
        import config
        from app.services import remnawave_api
        monkeypatch.setattr(config, "REMNAWAVE_API_URL", self.BASE)
        monkeypatch.setattr(config, "REMNAWAVE_API_TOKEN", "panel-token")
        monkeypatch.setattr(config, "REMNAWAVE_ENABLED", True)
        monkeypatch.setattr(config, "VPN_ENABLED", True)
        monkeypatch.setattr(remnawave_api, "httpx", self.httpx_shim())
        return self


__all__ = ["FakeRemnawaveHTTP", "GIB", "parse_dt"]
