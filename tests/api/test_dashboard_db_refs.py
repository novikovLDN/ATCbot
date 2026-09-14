"""Every `database.<fn>(` / `remnawave_api.<fn>(` call in dashboard code
must resolve.

Dead-code cleanups prune re-exports from database/__init__.py by looking
for importers inside the bot; the dashboard calls them through the
package attribute (`database.get_payments_breakdown(...)`), which a grep
for imports does not see. A missing name is an AttributeError at request
time — this test turns it into a CI failure instead.
"""
import re
from pathlib import Path

import database
from app.services import remnawave_api

ROOT = Path(__file__).resolve().parents[2]
FILES = sorted((ROOT / "app/api/dashboard").rglob("*.py")) + [
    ROOT / "app/services/panel_stats.py",
    ROOT / "app/services/system_health.py",
    ROOT / "database/metrics.py",
    ROOT / "database/revenue.py",
]
MODULES = {"database": database, "remnawave_api": remnawave_api}


def _calls():
    for f in FILES:
        if not f.exists():
            continue
        src = f.read_text(encoding="utf-8")
        for mod in MODULES:
            for name in re.findall(rf"\b{mod}\.([a-z_][a-z0-9_]*)\(", src):
                yield f.relative_to(ROOT).as_posix(), mod, name


def test_dashboard_calls_resolve():
    missing = sorted({(f, m, n) for f, m, n in _calls() if not hasattr(MODULES[m], n)})
    assert not missing, f"dashboard calls names that no longer exist: {missing}"


def test_scan_is_not_empty():
    assert sum(1 for _ in _calls()) > 50
