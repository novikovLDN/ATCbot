"""require_admin — who may call the dashboard API.

1. Session cookie (primary): set by /auth/login, /auth/setup or passkey
   login; opaque token in Redis / memory, 5-day TTL.
2. Bearer magic-link JWT — ONLY in the bootstrap state, i.e. while no
   password AND no passkey exist, and only a fresh link (≤ 15 minutes old,
   see auth.verify_bootstrap_token). Once the admin has a password or a
   passkey the link is inert for the API: it only opens the login screen.
   Before v3 a 30-day link was full API access forever (recon P1-1).
"""
from typing import Optional

from fastapi import Cookie, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.dashboard.auth import in_bootstrap_state, verify_bootstrap_token
from app.services import admin_auth

_bearer = HTTPBearer(auto_error=False)


async def require_admin(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    atlas_admin_session: Optional[str] = Cookie(default=None),
) -> dict:
    if atlas_admin_session:
        tg = await admin_auth.lookup_session(atlas_admin_session)
        if tg is not None and admin_auth.is_admin(tg):
            return {"sub": tg, "role": "admin", "auth": "session"}

    if creds and creds.credentials:
        if not await in_bootstrap_state():
            raise HTTPException(401, "bearer_not_accepted")
        payload = verify_bootstrap_token(creds.credentials)
        if not payload:
            raise HTTPException(401, "invalid_or_expired_token")
        sub_id = int(payload["sub"])
        if not admin_auth.is_admin(sub_id):
            raise HTTPException(403, "forbidden")
        return {"sub": sub_id, "role": "admin", "auth": "bootstrap"}

    raise HTTPException(401, "not_authenticated")
