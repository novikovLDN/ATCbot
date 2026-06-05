"""Dashboard auth endpoints.

Flow:
  /admin (bot)  ─→  magic-link URL  ─→  browser opens /dashboard/
        │
        ▼
        - GET /api/auth/status                       (no auth required)
            { has_password, has_session }
        ▼
   ┌─ no password ─────┐    ┌─ has password ────┐
   │ Setup form        │    │ Login form        │
   │ POST /auth/setup  │    │ POST /auth/login  │
   │ (bearer JWT       │    │ (username,        │
   │  bootstrap)       │    │  password)        │
   └────────┬──────────┘    └────────┬──────────┘
            └─────── HttpOnly cookie ───────┘
                            │
                            ▼
                       Dashboard

After password is set, magic-link tokens NO LONGER let anyone in. They
only work as a one-time bootstrap (when no creds exist) or as an
admin-side recovery bridge (after pressing "Восстановить пароль" in
the bot, which clears creds → setup form reappears).
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

import config
from app.services import admin_auth

router = APIRouter()

_JWT_ALG = "HS256"
# Magic-link is intentionally long-lived: it's a bootstrap link that
# only does anything when there's no password set (or right after
# the admin pressed "Восстановить пароль"). Outside those windows
# it's inert, so giving it a long TTL just saves the admin from
# re-pressing /admin if they delay setup.
_MAGIC_TTL_DAYS = 30


def issue_login_token(admin_telegram_id: int) -> str:
    """Sign a long-lived bootstrap token. Called from the /admin
    bot handler."""
    if not config.JWT_SECRET:
        raise RuntimeError("JWT_SECRET is not configured")
    payload = {
        "sub": str(admin_telegram_id),
        "role": "admin",
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(days=_MAGIC_TTL_DAYS),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=_JWT_ALG)


def verify_token(token: str) -> Optional[dict[str, Any]]:
    if not config.JWT_SECRET or not token:
        return None
    try:
        return jwt.decode(token, config.JWT_SECRET, algorithms=[_JWT_ALG])
    except jwt.PyJWTError as e:
        import logging
        logging.getLogger(__name__).info(
            "DASHBOARD_JWT_VERIFY_FAIL %s: %s", type(e).__name__, e,
        )
        return None


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
    # SameSite=Lax keeps the cookie around across normal navigations
    # but not on cross-site POSTs — fine for a same-origin SPA.
    # secure=True is required on iOS PWA + most browsers since
    # standalone PWAs always run over HTTPS.
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
    response.delete_cookie(
        key=admin_auth.COOKIE_NAME, path="/dashboard/",
    )


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/status")
async def auth_status(
    request: Request,
    atlas_admin_session: Optional[str] = Cookie(default=None),
):
    """Public — used by the SPA on mount to decide which screen to
    render. NEVER requires auth, but tells the SPA whether it has a
    valid session, whether a password is set, and whether at least
    one passkey is registered (drives the "Войти через Face ID"
    button)."""
    has_password = await admin_auth.credentials_exist()
    has_session = False
    if atlas_admin_session:
        tg = await admin_auth.lookup_session(atlas_admin_session)
        has_session = tg is not None and admin_auth.is_admin(tg)
    has_passkey = False
    try:
        from app.services import admin_passkeys
        has_passkey = (await admin_passkeys.passkey_count()) > 0
    except Exception:
        pass
    return {
        "has_password": has_password,
        "has_session": has_session,
        "has_passkey": has_passkey,
    }


@router.post("/setup")
async def auth_setup(body: SetupRequest, response: Response):
    """Set username + password. Only allowed when (a) no password
    is set yet OR (b) the password has just been cleared by the bot's
    reset button. In both cases the caller must present a valid
    bootstrap JWT (from /admin) so a stranger who hits /api/auth/setup
    without ever having received the magic-link can't take over."""
    payload = verify_token(body.bootstrap_token)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(401, "invalid_bootstrap_token")
    sub = payload.get("sub")
    try:
        tg = int(sub) if sub is not None else 0
    except (TypeError, ValueError):
        tg = 0
    if not admin_auth.is_admin(tg):
        raise HTTPException(403, "not_admin")

    if await admin_auth.credentials_exist():
        # Setup is one-shot. To change creds, the bot must reset first.
        raise HTTPException(409, "already_setup")

    ok = await admin_auth.set_credentials(body.username, body.password)
    if not ok:
        raise HTTPException(500, "setup_failed")

    token = await admin_auth.create_session(tg)
    _set_session_cookie(response, token)
    return {"ok": True}


@router.post("/login")
async def auth_login(body: LoginRequest, response: Response):
    creds = await admin_auth.get_credentials()
    if not creds:
        raise HTTPException(409, "password_not_set")
    # Constant-time-ish compare: always do the hash check even if the
    # username is wrong so timing doesn't leak info.
    username_ok = body.username.strip().lower() == str(creds["username"]).strip().lower()
    password_ok = admin_auth.verify_password(body.password, str(creds["password_hash"]))
    if not (username_ok and password_ok):
        raise HTTPException(401, "invalid_credentials")

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
async def auth_me(
    atlas_admin_session: Optional[str] = Cookie(default=None),
):
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
async def passkey_register_options(
    _tg: int = Depends(_require_session),
):
    from app.services import admin_passkeys
    creds = await admin_auth.get_credentials()
    username = str((creds or {}).get("username") or "atlas-admin")
    try:
        options, token = await admin_passkeys.make_registration_options(username)
    except Exception as e:
        raise HTTPException(500, f"register_options_failed: {e}")
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
        raise HTTPException(500, f"auth_options_failed: {e}")
    return {"options": options, "challenge_token": token}


@router.post("/passkey/auth/verify")
async def passkey_auth_verify(
    body: PasskeyAuthVerifyRequest,
    response: Response,
):
    from app.services import admin_passkeys
    ok, err = await admin_passkeys.verify_authentication(
        challenge_token=body.challenge_token,
        credential=body.credential,
    )
    if not ok:
        raise HTTPException(401, f"auth_failed: {err}")
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


# Backwards-compatibility for the original /verify endpoint — kept so
# existing magic-link URLs from previous deploys don't break before
# the SPA reload.
@router.get("/verify")
async def verify_endpoint(token: str):
    payload = verify_token(token)
    if not payload:
        raise HTTPException(401, "Invalid or expired token")
    sub = payload.get("sub")
    try:
        telegram_id = int(sub) if sub is not None else None
    except (TypeError, ValueError):
        telegram_id = sub
    return {
        "telegram_id": telegram_id,
        "role": payload.get("role"),
        "expires_at": payload.get("exp"),
    }
