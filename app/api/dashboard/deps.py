"""require_admin — accepts either:
  - session cookie (new, primary)  — set by /api/auth/login or /setup
  - Bearer JWT (legacy)             — kept for curl / API testing

Cookie wins if both are present.
"""
from typing import Optional

from fastapi import Cookie, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import config
from app.api.dashboard.auth import verify_token
from app.services import admin_auth

_bearer = HTTPBearer(auto_error=False)


async def require_admin(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    atlas_admin_session: Optional[str] = Cookie(default=None),
) -> dict:
    # 1) Cookie session
    if atlas_admin_session:
        tg = await admin_auth.lookup_session(atlas_admin_session)
        if tg is not None and admin_auth.is_admin(tg):
            return {"sub": tg, "role": "admin", "auth": "session"}

    # 2) Bearer (legacy)
    if creds and creds.credentials:
        payload = verify_token(creds.credentials)
        if not payload:
            raise HTTPException(401, "Invalid or expired token")
        if payload.get("role") != "admin":
            raise HTTPException(403, "Forbidden")
        try:
            sub_id = int(payload["sub"])
        except (TypeError, ValueError, KeyError):
            raise HTTPException(401, "Invalid subject")
        if not admin_auth.is_admin(sub_id):
            raise HTTPException(403, "Forbidden")
        return {"sub": sub_id, "role": "admin", "auth": "bearer"}

    raise HTTPException(401, "Missing bearer token")
