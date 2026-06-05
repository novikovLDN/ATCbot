"""
FastAPI dependency: require a valid admin JWT.

Usage:
    from app.api.dashboard.deps import require_admin
    router = APIRouter(dependencies=[Depends(require_admin)])
"""
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.dashboard.auth import verify_token

_bearer = HTTPBearer(auto_error=False)


def require_admin(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    if not creds or not creds.credentials:
        raise HTTPException(401, "Missing bearer token")
    payload = verify_token(creds.credentials)
    if not payload:
        raise HTTPException(401, "Invalid or expired token")
    if payload.get("role") != "admin":
        raise HTTPException(403, "Forbidden")
    return payload
