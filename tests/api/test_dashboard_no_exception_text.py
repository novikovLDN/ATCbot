"""P2-16 (rest of it): dashboard 500s put the exception text into the response
(`HTTPException(500, f"<code>: {e}")` in ~96 places): SQL fragments, DSNs,
driver and provider messages reached the client. They now return only the
stable code; the traceback goes to the log (app.api.dashboard.errors).
"""
import glob
import re

import pytest

from app.api.dashboard.errors import server_error

LEAK = re.compile(r'HTTPException\(\s*500\s*,\s*f"[^"]*\{e\}')


def test_no_500_carries_the_exception_text():
    offenders = []
    for path in sorted(glob.glob("app/api/dashboard/routes/*.py")):
        for i, line in enumerate(open(path, encoding="utf-8"), 1):
            if LEAK.search(line):
                offenders.append(f"{path}:{i}")
    assert offenders == []


def test_server_error_is_code_only_and_logged(caplog):
    try:
        raise RuntimeError("password=hunter2 host=db.internal")
    except RuntimeError:
        err = server_error("export_failed")
    assert err.status_code == 500 and err.detail == "export_failed"
    assert "DASHBOARD_ROUTE_FAIL export_failed" in caplog.text


@pytest.mark.parametrize("path", sorted(glob.glob("app/api/dashboard/routes/*.py")))
def test_route_modules_import(path):
    import importlib
    mod = path[:-3].replace("/", ".")
    importlib.import_module(mod)
