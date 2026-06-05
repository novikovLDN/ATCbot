"""
Dashboard auth — JWT issued by /admin bot command, verified here.

Flow:
  1. Admin types /admin in Telegram chat.
  2. Bot handler (app.handlers.admin.base) checks ADMIN_TELEGRAM_ID,
     calls `issue_login_token()`, builds URL `{DASHBOARD_BASE_URL}/dashboard/?login=<jwt>`,
     sends as inline-button.
  3. Browser opens URL, JS captures `?login=` and stores token in
     localStorage.
  4. Every REST call sends Authorization: Bearer <token>.
  5. WebSocket passes the token via `?token=` query param.

Tokens are short-lived (10 min). Refresh = press /admin again. The
audience is one person — the project admin — so we don't need a
refresh-token dance.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
from fastapi import APIRouter, HTTPException

import config

router = APIRouter()

_JWT_ALG = "HS256"
_TOKEN_TTL_MINUTES = 10


def issue_login_token(admin_telegram_id: int) -> str:
    """Sign a short-lived admin token. Called from the /admin bot handler.

    PyJWT 2.10+ enforces `sub` to be a string at decode time
    (InvalidSubjectError otherwise). Encoding does NOT validate, so the
    token gets issued fine — but every subsequent verify fails. Cast to
    str here; callers that need the integer telegram_id read it as
    `int(payload["sub"])` (users.py already does).
    """
    if not config.JWT_SECRET:
        raise RuntimeError("JWT_SECRET is not configured")
    payload = {
        "sub": str(admin_telegram_id),
        "role": "admin",
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=_TOKEN_TTL_MINUTES),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=_JWT_ALG)


def verify_token(token: str) -> Optional[dict[str, Any]]:
    """Decode + validate. Returns payload dict on success, None on any failure.

    Logs the specific PyJWT error so misconfigurations (wrong secret,
    clock skew, sub-claim format) surface in Railway logs instead of
    being silently swallowed as a 401.
    """
    if not config.JWT_SECRET:
        return None
    try:
        return jwt.decode(token, config.JWT_SECRET, algorithms=[_JWT_ALG])
    except jwt.PyJWTError as e:
        import logging
        logging.getLogger(__name__).info(
            "DASHBOARD_JWT_VERIFY_FAIL %s: %s", type(e).__name__, e,
        )
        return None


@router.get("/verify")
async def verify_endpoint(token: str):
    """Browser uses this once after grabbing ?login=<token> from URL —
    sanity-checks the token is well-formed before stashing in localStorage."""
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
