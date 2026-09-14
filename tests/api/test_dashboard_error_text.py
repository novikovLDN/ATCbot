"""P2-16: a failing legacy dashboard route answers 500 with a stable error
code; the exception text (SQL, DSN, driver message) stays in the log."""
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from fastapi import FastAPI  # noqa: E402

import database  # noqa: E402
from app.api.dashboard.deps import require_admin  # noqa: E402
from app.api.dashboard.routes import payments as payments_routes  # noqa: E402
from app.api.dashboard.routes import stats as stats_routes  # noqa: E402
from database import revenue as rev  # noqa: E402

SECRET = "password=hunter2 host=db.internal"


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(stats_routes.router, prefix="/stats")
    app.include_router(payments_routes.router, prefix="/payments")
    app.dependency_overrides[require_admin] = lambda: {"sub": 1, "role": "admin", "auth": "session"}
    return TestClient(app)


@pytest.mark.parametrize("path,target,attr,code", [
    ("/stats/revenue", rev, "all_time", "revenue_failed"),
    ("/stats/business", database, "get_business_metrics", "business_metrics_failed"),
    ("/payments/pending", database, "get_pending_payments", "pending_failed"),
    ("/payments/breakdown", database, "get_payments_breakdown", "breakdown_failed"),
])
def test_500_carries_code_not_exception_text(monkeypatch, path, target, attr, code):
    monkeypatch.setattr(target, attr, AsyncMock(side_effect=RuntimeError(SECRET)), raising=False)
    resp = _client().get(path)
    assert resp.status_code == 500
    assert resp.json() == {"detail": code}
    assert "hunter2" not in resp.text
