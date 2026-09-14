"""Import smoke test: every application module must import cleanly.

A missing module, a renamed symbol or a circular import in a module that no
unit test happens to touch would otherwise only surface on the next Railway
boot. Importing is enough to catch that class of error: no DB, no network,
no bot token validation happens at import time (config.py is satisfied by the
stub env in tests/conftest.py, which is loaded before this module).
"""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Entry points and the modules the production process wires together.
ENTRY_MODULES = [
    "config",
    "migrations",
    "database",
    "main",
    "app.api",
    "app.api.dashboard",
    "app.handlers",
]

# Packages whose every submodule is imported.
WALKED_PACKAGES = ["app", "database"]

# Root-level modules that are part of the running bot (imported by main.py
# or by handlers/workers). Standalone CLI tools live in scripts/ and are not
# shipped in the image (.dockerignore), so they are not listed here.
ROOT_MODULES = sorted(
    p.stem
    for p in REPO_ROOT.glob("*.py")
    if p.stem not in {"conftest", "setup"}
)


def _walk(package_name: str) -> list[str]:
    package = importlib.import_module(package_name)
    names = [package_name]
    for info in pkgutil.walk_packages(package.__path__, prefix=f"{package_name}."):
        names.append(info.name)
    return names


def _all_modules() -> list[str]:
    seen: dict[str, None] = {}
    for name in ENTRY_MODULES + ROOT_MODULES:
        seen.setdefault(name, None)
    for pkg in WALKED_PACKAGES:
        for name in _walk(pkg):
            seen.setdefault(name, None)
    return list(seen)


def test_root_modules_discovered():
    # Guard against the glob silently matching nothing (wrong cwd/layout).
    assert "main" in ROOT_MODULES
    assert "config" in ROOT_MODULES


@pytest.mark.parametrize("module_name", _all_modules())
def test_module_imports(module_name: str):
    importlib.import_module(module_name)


def test_fastapi_app_builds():
    """app.api builds the FastAPI app; the dashboard routers are populated.

    The dashboard is only *mounted* on the app when JWT_SECRET and
    DASHBOARD_BASE_URL are configured (app/api/__init__.py), so the test
    checks the routers themselves rather than the mount.
    """
    api = importlib.import_module("app.api")
    paths = {getattr(r, "path", "") for r in api.app.routes}
    assert "/health" in paths, sorted(paths)

    dashboard = importlib.import_module("app.api.dashboard")
    assert len(dashboard.router.routes) > 0
    assert len(dashboard.ws_router.routes) > 0
