"""
Unit tests for scripts/migrate_samopis_to_remnawave.py.

Covers internals that don't require a real DB or panel:
  - _RateLimiter pacing (uses asyncio.sleep mocked)
  - CSV log header + row writing
  - _validate_apply_config refuses runs without squad / token
  - LogRow dataclass round-trip

Heavy paths (_process_one, _run) are tested via mocks of remnawave_premium
and database to avoid a real network or PostgreSQL.
"""
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import asyncio
import csv
import importlib
import pytest


def _load():
    """Import the script module fresh (its path uses sys.path injection)."""
    return importlib.import_module("scripts.migrate_samopis_to_remnawave")


# ── _RateLimiter ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rate_limiter_paces_calls(monkeypatch):
    mod = _load()
    rl = mod._RateLimiter(rps=10.0)  # 100 ms gap

    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)

    # First call sets the baseline — no sleep needed
    await rl.acquire()
    await rl.acquire()
    await rl.acquire()

    # We expect at least two paced sleeps (calls 2 and 3); some may be
    # near-zero depending on the host clock, but none can exceed min_interval.
    assert len(sleeps) >= 1
    for s in sleeps:
        assert s <= rl.min_interval + 0.001


def test_rate_limiter_rejects_zero():
    mod = _load()
    with pytest.raises(ValueError):
        mod._RateLimiter(rps=0)


# ── _CsvLog ────────────────────────────────────────────────────────────

def test_csv_log_writes_header_and_rows(tmp_path: Path):
    mod = _load()
    path = tmp_path / "out.csv"

    with mod._CsvLog(path, dry_run=False) as log:
        log.write(mod.LogRow(
            timestamp="2026-05-12T00:00:00Z",
            telegram_id=42,
            uuid_samopis="11111111-2222-3333-4444-555555555555",
            uuid_remnawave_bypass=None,
            uuid_remnawave_premium="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            forced_uuid_accepted=True,
            status="ok",
            http_status=201,
            subscription_url="https://r/sub/x",
            error=None,
        ))
        log.write(mod.LogRow(
            timestamp="2026-05-12T00:00:01Z",
            telegram_id=43,
            uuid_samopis="abc",
            uuid_remnawave_bypass=None,
            uuid_remnawave_premium=None,
            forced_uuid_accepted=False,
            status="failed",
            http_status=400,
            subscription_url=None,
            error="bad uuid",
        ))

    content = path.read_text(encoding="utf-8").splitlines()
    assert content[0].split(",") == mod.CSV_FIELDS
    assert "42" in content[1]
    assert "ok" in content[1]
    assert "failed" in content[2]


def test_csv_log_appends_without_duplicate_header(tmp_path: Path):
    """Resumed runs append to the same CSV — header must appear exactly once."""
    mod = _load()
    path = tmp_path / "out.csv"

    row = mod.LogRow(
        timestamp="t", telegram_id=1, uuid_samopis="u",
        uuid_remnawave_bypass=None, uuid_remnawave_premium=None,
        forced_uuid_accepted=False, status="dry-run",
        http_status=0, subscription_url=None, error=None,
    )

    with mod._CsvLog(path, dry_run=True) as log:
        log.write(row)
    with mod._CsvLog(path, dry_run=True) as log:
        log.write(row)

    lines = path.read_text(encoding="utf-8").splitlines()
    header_lines = [l for l in lines if l.startswith("timestamp,telegram_id")]
    assert len(header_lines) == 1
    # Two data rows plus one header
    assert len(lines) == 3


# ── _validate_apply_config ─────────────────────────────────────────────

def test_validate_apply_config_blocks_without_token():
    mod = _load()
    with patch.object(mod, "config") as cfg:
        cfg.REMNAWAVE_ENABLED = False
        assert mod._validate_apply_config() is not None


def test_validate_apply_config_blocks_without_main_squad():
    mod = _load()
    with patch.object(mod, "config") as cfg:
        cfg.REMNAWAVE_ENABLED = True
        cfg.REMNAWAVE_MAIN_SQUAD_UUID = ""
        problem = mod._validate_apply_config()
        assert problem is not None
        assert "MAIN_SQUAD" in problem


def test_validate_apply_config_ok():
    mod = _load()
    with patch.object(mod, "config") as cfg:
        cfg.REMNAWAVE_ENABLED = True
        cfg.REMNAWAVE_MAIN_SQUAD_UUID = "uuid-here"
        assert mod._validate_apply_config() is None


# ── _process_one ──────────────────────────────────────────────────────

SAMOPIS_UUID = "11111111-2222-3333-4444-555555555555"
PANEL_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _row(**over):
    base = {
        "telegram_id": 42,
        "uuid": SAMOPIS_UUID,
        "remnawave_uuid": None,
        "remnawave_premium_uuid": None,
        "subscription_type": "basic",
        "expires_at": datetime(2030, 1, 1, tzinfo=timezone.utc),
        "status": "active",
        "samopis_migrated_at": None,
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_process_one_dry_run_skips_api_and_db():
    mod = _load()
    rl = mod._RateLimiter(rps=1000)

    create_mock = AsyncMock()
    persist_mock = AsyncMock()
    with patch.object(mod.remnawave_premium, "create_premium_user_entity", create_mock), \
         patch.object(mod, "database") as db_mock:
        db_mock.set_remnawave_premium_uuid = persist_mock
        out = await mod._process_one(_row(), apply=False, rate_limiter=rl)

    create_mock.assert_not_called()
    persist_mock.assert_not_called()
    assert out.status == "dry-run"
    assert out.uuid_samopis == SAMOPIS_UUID


@pytest.mark.asyncio
async def test_process_one_apply_persists_panel_uuid():
    mod = _load()
    rl = mod._RateLimiter(rps=1000)

    fake_result = mod.remnawave_premium.PremiumCreateResult(
        ok=True,
        panel_uuid=PANEL_UUID,
        forced_uuid_accepted=False,
        subscription_url="https://r/sub/x",
        status=201,
        error=None,
    )
    create_mock = AsyncMock(return_value=fake_result)
    persist_mock = AsyncMock()
    with patch.object(mod.remnawave_premium, "create_premium_user_entity", create_mock), \
         patch.object(mod, "database") as db_mock:
        db_mock.set_remnawave_premium_uuid = persist_mock
        out = await mod._process_one(_row(), apply=True, rate_limiter=rl)

    create_mock.assert_awaited_once()
    persist_mock.assert_awaited_once_with(42, PANEL_UUID)
    assert out.status == "ok"
    assert out.uuid_remnawave_premium == PANEL_UUID
    assert out.subscription_url == "https://r/sub/x"


@pytest.mark.asyncio
async def test_process_one_apply_records_failure_without_db_write():
    mod = _load()
    rl = mod._RateLimiter(rps=1000)

    fake_result = mod.remnawave_premium.PremiumCreateResult(
        ok=False,
        panel_uuid=None,
        forced_uuid_accepted=False,
        subscription_url=None,
        status=400,
        error="bad-uuid",
    )
    create_mock = AsyncMock(return_value=fake_result)
    persist_mock = AsyncMock()
    with patch.object(mod.remnawave_premium, "create_premium_user_entity", create_mock), \
         patch.object(mod, "database") as db_mock:
        db_mock.set_remnawave_premium_uuid = persist_mock
        out = await mod._process_one(_row(), apply=True, rate_limiter=rl)

    persist_mock.assert_not_awaited()
    assert out.status == "failed"
    assert out.http_status == 400
    assert out.error == "bad-uuid"


@pytest.mark.asyncio
async def test_process_one_apply_db_persist_error_marks_failure():
    mod = _load()
    rl = mod._RateLimiter(rps=1000)

    fake_result = mod.remnawave_premium.PremiumCreateResult(
        ok=True, panel_uuid=PANEL_UUID, forced_uuid_accepted=False,
        subscription_url=None, status=201, error=None,
    )
    create_mock = AsyncMock(return_value=fake_result)
    persist_mock = AsyncMock(side_effect=RuntimeError("db gone"))
    with patch.object(mod.remnawave_premium, "create_premium_user_entity", create_mock), \
         patch.object(mod, "database") as db_mock:
        db_mock.set_remnawave_premium_uuid = persist_mock
        out = await mod._process_one(_row(), apply=True, rate_limiter=rl)

    assert out.status == "failed"
    assert "db_persist_error" in (out.error or "")
    # The panel uuid is still recorded in the CSV so a follow-up run can recover
    assert out.uuid_remnawave_premium == PANEL_UUID
