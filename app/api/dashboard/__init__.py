"""
Admin web dashboard — FastAPI routers mounted under /dashboard/.

Layout:
  /dashboard/api/auth/*    — login + token verification
  /dashboard/api/stats/*   — analytics, business metrics
  /dashboard/api/users/*   — user lookup, grants, balance, discounts
  /dashboard/ws            — WebSocket fan-out from app.events.bus

All routes (except auth/verify) require a valid admin JWT — see
app.api.dashboard.deps.require_admin.

Gated by config.DASHBOARD_ENABLED: when JWT_SECRET or DASHBOARD_BASE_URL
isn't set, app.api.__init__ never mounts these routers.
"""
from fastapi import APIRouter

from app.api.dashboard.routes import stats as _stats
from app.api.dashboard.routes import users as _users
from app.api.dashboard.routes import audit as _audit
from app.api.dashboard.routes import broadcasts as _broadcasts
from app.api.dashboard import auth as _auth
from app.api.dashboard import ws as _ws

# Public REST router — mounted at /dashboard/api in app/api/__init__.py.
router = APIRouter()
router.include_router(_auth.router, prefix="/auth", tags=["auth"])
router.include_router(_stats.router, prefix="/stats", tags=["stats"])
router.include_router(_users.router, prefix="/users", tags=["users"])
router.include_router(_audit.router, prefix="/audit", tags=["audit"])
router.include_router(_broadcasts.router, prefix="/broadcasts", tags=["broadcasts"])

# Separate router for the WebSocket endpoint — FastAPI requires WS
# routes to be on a router (or app) that hasn't had a `prefix` applied
# in include_router; mount it directly at /dashboard.
ws_router = _ws.router
