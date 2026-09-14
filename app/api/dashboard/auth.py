"""Dashboard auth endpoints.

Flow (docs/dashboard/auth.md):
  /admin (bot)  ─→  magic-link URL (valid 15 min)  ─→  /dashboard/?login=<jwt>
        │
        ▼
        GET /api/auth/status   (public)  { has_password, has_session, has_passkey }
        ▼
   ┌─ no password, no passkey ─┐    ┌─ password / passkey exist ─┐
   │ Setup form                │    │ Login form / Face ID       │
   │ POST /auth/setup          │    │ POST /auth/login           │
   │ (magic-link token in body)│    │ POST /auth/passkey/auth/*  │
   └──────────┬────────────────┘    └──────────┬─────────────────┘
              └──────── HttpOnly session cookie ┘
                               ▼
                           Dashboard

The magic link is a bootstrap / recovery device only: it can set the
first password (or a new one after "Сбросить пароль" in the bot wiped the
credentials) within 15 minutes of being issued. It is never general API
auth once a password or passkey exists (deps.require_admin).

Login, setup and passkey verification are rate-limited with a lockout
(app/api/dashboard/security.py). Every state-changing request passes the
Origin/Referer CSRF check (router-level dependency in __init__).
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

import config
from app.api.dashboard import security
from app.branding import get_brand
from app.services import admin_auth

logger = logging.getLogger(__name__)

router = APIRouter()

_JWT_ALG = "HS256"
# Bootstrap links are short-lived. The bot re-issues one on every /admin,
# so a 15-minute window costs the admin nothing and a leaked link (chat
# export, screenshot, shared device) goes stale almost immediately.
MAGIC_TTL = timedelta(minutes=15)
_MAGIC_TYP = "magic"


def issue_login_token(admin_telegram_id: int) -> str:
    """Sign a short-lived bootstrap token. Called from the /admin bot handler."""
    if not config.JWT_SECRET:
        raise RuntimeError("JWT_SECRET is not configured")
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(admin_telegram_id),
        "role": "admin",
        "typ": _MAGIC_TYP,
        "iat": now,
        "exp": now + MAGIC_TTL,
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=_JWT_ALG)


def verify_token(token: str) -> Optional[dict[str, Any]]:
    """Signature + expiry only. Prefer verify_bootstrap_token."""
    if not config.JWT_SECRET or not token:
        return None
    try:
        return jwt.decode(token, config.JWT_SECRET, algorithms=[_JWT_ALG])
    except jwt.PyJWTError as e:
        logger.info("DASHBOARD_JWT_VERIFY_FAIL %s", type(e).__name__)
        return None


def verify_bootstrap_token(token: str) -> Optional[dict[str, Any]]:
    """A magic-link token that is valid right now for bootstrap.

    Checks the issue time, not just `exp`: links minted by the previous
    build carried a 30-day expiry and must not keep working for a month.
    """
    payload = verify_token(token)
    if not payload or payload.get("role") != "admin":
        return None
    iat = payload.get("iat")
    try:
        issued = datetime.fromtimestamp(int(iat), tz=timezone.utc)
    except (TypeError, ValueError):
        return None
    now = datetime.now(timezone.utc)
    if issued > now + timedelta(minutes=1) or now - issued > MAGIC_TTL:
        return None
    try:
        int(payload.get("sub"))
    except (TypeError, ValueError):
        return None
    return payload


async def _passkey_count() -> int:
    try:
        from app.services import admin_passkeys
        return int(await admin_passkeys.passkey_count())
    except Exception:
        return 0


async def in_bootstrap_state() -> bool:
    """No password and no passkey: the only state a magic link may act in."""
    if await admin_auth.credentials_exist():
        return False
    return await _passkey_count() == 0


# ── Models ────────────────────────────────────────────────────────────


class SetupRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=40)
    password: str = Field(..., min_length=8, max_length=200)
    bootstrap_token: str = Field(..., min_length=10)

    @field_validator("username")
    @classmethod
    def _u(cls, v: str) -> str:
        v = v.strip()
        if not v.replace("_", "").replace("-", "").replace(".", "").isalnum():
            raise ValueError("username must be alphanumeric (._- allowed)")
        return v


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=40)
    password: str = Field(..., min_length=1, max_length=200)


# ── Helpers ──────────────────────────────────────────────────────────


def _set_session_cookie(response: Response, token: str) -> None:
    # SameSite=Lax keeps the cookie on top-level navigations (the magic
    # link opens from Telegram) and off cross-site subrequests; the CSRF
    # check covers the rest. secure=True: iOS PWA runs over HTTPS only.
    response.set_cookie(
        key=admin_auth.COOKIE_NAME,
        value=token,
        max_age=admin_auth.SESSION_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/dashboard/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=admin_auth.COOKIE_NAME, path="/dashboard/")


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/status")
async def auth_status(atlas_admin_session: Optional[str] = Cookie(default=None)):
    """Public — decides which screen the SPA renders."""
    has_password = await admin_auth.credentials_exist()
    has_session = False
    if atlas_admin_session:
        tg = await admin_auth.lookup_session(atlas_admin_session)
        has_session = tg is not None and admin_auth.is_admin(tg)
    return {
        "has_password": has_password,
        "has_session": has_session,
        "has_passkey": (await _passkey_count()) > 0,
    }


@router.post("/setup")
async def auth_setup(body: SetupRequest, request: Request, response: Response):
    """Set username + password. Allowed only in the bootstrap state and
    only with a fresh magic-link token from /admin."""
    ip = security.client_ip(request)
    await security.ensure_not_locked((security.SETUP_IP, ip))

    payload = verify_bootstrap_token(body.bootstrap_token)
    if not payload:
        await security.fail((security.SETUP_IP, ip))
        raise HTTPException(401, "invalid_bootstrap_token")
    tg = int(payload["sub"])
    if not admin_auth.is_admin(tg):
        await security.fail((security.SETUP_IP, ip))
        raise HTTPException(403, "not_admin")

    if not await in_bootstrap_state():
        # Setup is one-shot. To change creds, the bot must reset first.
        raise HTTPException(409, "already_setup")

    if not await admin_auth.set_credentials(body.username, body.password):
        raise HTTPException(500, "setup_failed")

    await security.reset(security.SETUP_IP, ip)
    token = await admin_auth.create_session(tg)
    _set_session_cookie(response, token)
    logger.info("DASHBOARD_AUTH_SETUP ok")
    return {"ok": True}


@router.post("/login")
async def auth_login(body: LoginRequest, request: Request, response: Response):
    ip = security.client_ip(request)
    user_key = body.username.strip().lower()
    checks = ((security.LOGIN_IP, ip), (security.LOGIN_USER, user_key))
    await security.ensure_not_locked(*checks)

    creds = await admin_auth.get_credentials()
    if not creds:
        raise HTTPException(409, "password_not_set")
    # P1-6: count the attempt BEFORE the password check (increment-then-check):
    # the "not locked" check above and the await before it let N parallel
    # requests all reach bcrypt; now at most max_failures per window do.
    counts = await security.begin_attempt(*checks)
    # Always run the hash check, even for a wrong username, so timing
    # does not reveal which half was wrong.
    username_ok = user_key == str(creds["username"]).strip().lower()
    # bcrypt (cost 12, ~0.25 s) off the event loop: bot, webhooks and workers
    # share this process and must not stall on an unauthenticated endpoint.
    password_ok = await asyncio.to_thread(
        admin_auth.verify_password, body.password, str(creds["password_hash"])
    )
    if not (username_ok and password_ok):
        await security.attempt_failed(checks, counts)
        logger.warning("DASHBOARD_AUTH_LOGIN_FAIL")
        raise HTTPException(401, "invalid_credentials")

    for policy, ident in checks:
        await security.reset(policy, ident)
    token = await admin_auth.create_session(config.ADMIN_TELEGRAM_ID)
    _set_session_cookie(response, token)
    return {"ok": True}


@router.post("/logout")
async def auth_logout(
    response: Response,
    atlas_admin_session: Optional[str] = Cookie(default=None),
):
    if atlas_admin_session:
        await admin_auth.revoke_session(atlas_admin_session)
    _clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
async def auth_me(atlas_admin_session: Optional[str] = Cookie(default=None)):
    if not atlas_admin_session:
        raise HTTPException(401, "no_session")
    tg = await admin_auth.lookup_session(atlas_admin_session)
    if tg is None or not admin_auth.is_admin(tg):
        raise HTTPException(401, "invalid_session")
    return {"telegram_id": tg}


# ── Passkey (WebAuthn) ───────────────────────────────────────────────


class PasskeyRegisterVerifyRequest(BaseModel):
    challenge_token: str = Field(..., min_length=8)
    credential: dict
    label: Optional[str] = Field(None, max_length=64)


class PasskeyAuthVerifyRequest(BaseModel):
    challenge_token: str = Field(..., min_length=8)
    credential: dict


async def _require_session(
    atlas_admin_session: Optional[str] = Cookie(default=None),
) -> int:
    if not atlas_admin_session:
        raise HTTPException(401, "no_session")
    tg = await admin_auth.lookup_session(atlas_admin_session)
    if tg is None or not admin_auth.is_admin(tg):
        raise HTTPException(401, "invalid_session")
    return tg


@router.post("/passkey/register/options")
async def passkey_register_options(_tg: int = Depends(_require_session)):
    from app.services import admin_passkeys
    creds = await admin_auth.get_credentials()
    username = str((creds or {}).get("username") or f"{get_brand().slug}-admin")
    try:
        options, token = await admin_passkeys.make_registration_options(username)
    except Exception as e:
        logger.warning("passkey register options failed: %s", e)
        raise HTTPException(500, "register_options_failed")
    return {"options": options, "challenge_token": token}


@router.post("/passkey/register/verify")
async def passkey_register_verify(
    body: PasskeyRegisterVerifyRequest,
    _tg: int = Depends(_require_session),
):
    from app.services import admin_passkeys
    ok, err = await admin_passkeys.verify_and_store_registration(
        challenge_token=body.challenge_token,
        credential=body.credential,
        label=body.label,
    )
    if not ok:
        raise HTTPException(400, f"register_failed: {err}")
    return {"ok": True}


@router.post("/passkey/auth/options")
async def passkey_auth_options():
    from app.services import admin_passkeys
    if await admin_passkeys.passkey_count() == 0:
        raise HTTPException(409, "no_passkeys_registered")
    try:
        options, token = await admin_passkeys.make_authentication_options()
    except Exception as e:
        logger.warning("passkey auth options failed: %s", e)
        raise HTTPException(500, "auth_options_failed")
    return {"options": options, "challenge_token": token}


@router.post("/passkey/auth/verify")
async def passkey_auth_verify(
    body: PasskeyAuthVerifyRequest,
    request: Request,
    response: Response,
):
    from app.services import admin_passkeys
    ip = security.client_ip(request)
    await security.ensure_not_locked((security.PASSKEY_IP, ip))
    ok, err = await admin_passkeys.verify_authentication(
        challenge_token=body.challenge_token,
        credential=body.credential,
    )
    if not ok:
        await security.fail((security.PASSKEY_IP, ip))
        raise HTTPException(401, f"auth_failed: {err}")
    await security.reset(security.PASSKEY_IP, ip)
    token = await admin_auth.create_session(config.ADMIN_TELEGRAM_ID)
    _set_session_cookie(response, token)
    return {"ok": True}


@router.get("/passkey/list")
async def passkey_list(_tg: int = Depends(_require_session)):
    from app.services import admin_passkeys
    return await admin_passkeys.list_passkeys()


@router.delete("/passkey/{pk_id}")
async def passkey_delete(pk_id: int, _tg: int = Depends(_require_session)):
    from app.services import admin_passkeys
    ok = await admin_passkeys.delete_passkey(pk_id)
    if not ok:
        raise HTTPException(404, "not_found")
    return {"ok": True}

# The unauthenticated GET /verify?token=… endpoint was removed in v3: it
# let anyone probe whether a token was valid and echoed its claims.
