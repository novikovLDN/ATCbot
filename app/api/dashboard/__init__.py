"""
Admin web dashboard — FastAPI routers mounted under /dashboard/.

Layout:
  /dashboard/api/auth/*       — login / setup / passkeys (public entry points)
  /dashboard/api/branding     — public brand for the login screen
  /dashboard/api/metrics/*    — canonical business metrics (docs/dashboard/metrics.md)
  /dashboard/api/panel/*      — Remnawave panel stats, read-only
  /dashboard/api/…            — users, broadcasts, payments, settings, …
  /dashboard/ws               — WebSocket fan-out from app.events.bus (cookie auth)

Every route except auth entry points and branding requires
app.api.dashboard.deps.require_admin (declared on each sub-router).
Every router is included with the CSRF dependency: state-changing
requests must come from the dashboard's own origin
(app/api/dashboard/security.py).

Gated by config.DASHBOARD_ENABLED: when JWT_SECRET or DASHBOARD_BASE_URL
isn't set, app.api.__init__ never mounts these routers.
"""
from fastapi import APIRouter, Depends

from app.api.dashboard import auth as _auth
from app.api.dashboard import ws as _ws
from app.api.dashboard.routes import activations as _activations
from app.api.dashboard.routes import audit as _audit
from app.api.dashboard.routes import automated_notifications as _autonotif
from app.api.dashboard.routes import beta_applications as _beta_apps
from app.api.dashboard.routes import bgift as _bgift
from app.api.dashboard.routes import branding as _branding
from app.api.dashboard.routes import broadcasts as _broadcasts
from app.api.dashboard.routes import bypass_audit as _bypass_audit
from app.api.dashboard.routes import export as _export
from app.api.dashboard.routes import incident as _incident
from app.api.dashboard.routes import links as _links
from app.api.dashboard.routes import metrics as _metrics
from app.api.dashboard.routes import panel as _panel
from app.api.dashboard.routes import payments as _payments
from app.api.dashboard.routes import premium_repair as _premium_repair
from app.api.dashboard.routes import pricing as _pricing
from app.api.dashboard.routes import promo as _promo
from app.api.dashboard.routes import reconciliation as _reconciliation
from app.api.dashboard.routes import referrals as _referrals
from app.api.dashboard.routes import remnawave as _remnawave
from app.api.dashboard.routes import remnawave_tags as _remnawave_tags
from app.api.dashboard.routes import settings as _settings
from app.api.dashboard.routes import stats as _stats
from app.api.dashboard.routes import traffic_audit as _traffic_audit
from app.api.dashboard.routes import users as _users
from app.api.dashboard.security import csrf_protect

_SUBROUTERS = (
    (_auth.router, "/auth", "auth"),
    (_branding.router, "/branding", "branding"),
    (_stats.router, "/stats", "stats"),
    (_users.router, "/users", "users"),
    (_audit.router, "/audit", "audit"),
    (_broadcasts.router, "/broadcasts", "broadcasts"),
    (_export.router, "/export", "export"),
    (_referrals.router, "/referrals", "referrals"),
    (_bgift.router, "/bgift", "bgift"),
    (_incident.router, "/incident", "incident"),
    (_promo.router, "/promo", "promo"),
    (_payments.router, "/payments", "payments"),
    (_activations.router, "/activations", "activations"),
    (_settings.router, "/settings", "settings"),
    (_bypass_audit.router, "/bypass-audit", "bypass-audit"),
    (_traffic_audit.router, "/traffic-audit", "traffic-audit"),
    (_reconciliation.router, "/reconciliation", "reconciliation"),
    (_links.router, "/links", "links"),
    (_autonotif.router, "/automated-notifications", "automated-notifications"),
    (_pricing.router, "/pricing", "pricing"),
    (_beta_apps.router, "/beta-applications", "beta-applications"),
    (_remnawave.router, "/remnawave", "remnawave"),
    (_remnawave_tags.router, "/remnawave-tags", "remnawave-tags"),
    (_premium_repair.router, "/premium-repair", "premium-repair"),
    (_metrics.router, "/metrics", "metrics"),
    (_panel.router, "/panel", "panel"),
)

# Public REST router — mounted at /dashboard/api in app/api/__init__.py.
router = APIRouter()
for _r, _prefix, _tag in _SUBROUTERS:
    router.include_router(_r, prefix=_prefix, tags=[_tag], dependencies=[Depends(csrf_protect)])

# Separate router for the WebSocket endpoint — mounted at /dashboard.
ws_router = _ws.router
