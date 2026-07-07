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
from app.api.dashboard.routes import export as _export
from app.api.dashboard.routes import referrals as _referrals
from app.api.dashboard.routes import bgift as _bgift
from app.api.dashboard.routes import incident as _incident
from app.api.dashboard.routes import promo as _promo
from app.api.dashboard.routes import payments as _payments
from app.api.dashboard.routes import settings as _settings
from app.api.dashboard.routes import activations as _activations
from app.api.dashboard.routes import bypass_audit as _bypass_audit
from app.api.dashboard.routes import reconciliation as _reconciliation
from app.api.dashboard.routes import links as _links
from app.api.dashboard import auth as _auth
from app.api.dashboard import ws as _ws

# Public REST router — mounted at /dashboard/api in app/api/__init__.py.
router = APIRouter()
router.include_router(_auth.router, prefix="/auth", tags=["auth"])
router.include_router(_stats.router, prefix="/stats", tags=["stats"])
router.include_router(_users.router, prefix="/users", tags=["users"])
router.include_router(_audit.router, prefix="/audit", tags=["audit"])
router.include_router(_broadcasts.router, prefix="/broadcasts", tags=["broadcasts"])
router.include_router(_export.router, prefix="/export", tags=["export"])
router.include_router(_referrals.router, prefix="/referrals", tags=["referrals"])
router.include_router(_bgift.router, prefix="/bgift", tags=["bgift"])
router.include_router(_incident.router, prefix="/incident", tags=["incident"])
router.include_router(_promo.router, prefix="/promo", tags=["promo"])
router.include_router(_payments.router, prefix="/payments", tags=["payments"])
router.include_router(_activations.router, prefix="/activations", tags=["activations"])
router.include_router(_settings.router, prefix="/settings", tags=["settings"])
router.include_router(_bypass_audit.router, prefix="/bypass-audit", tags=["bypass-audit"])
router.include_router(_reconciliation.router, prefix="/reconciliation", tags=["reconciliation"])
router.include_router(_links.router, prefix="/links", tags=["links"])

# Separate router for the WebSocket endpoint — FastAPI requires WS
# routes to be on a router (or app) that hasn't had a `prefix` applied
# in include_router; mount it directly at /dashboard.
ws_router = _ws.router
