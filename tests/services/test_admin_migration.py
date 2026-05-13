"""
Unit tests for app.handlers.admin.migration internals.

Covers the testable pieces that don't require a full aiogram harness:
  - _run_script: subprocess execution, timeout, preflight on missing script
  - _format_output: header / truncation / empty-output handling
  - _send_csv_if_available: skipped / sent / oversized branches
  - Path constants (sync between writer and reader)
  - Download handler: file-missing / oversized / happy branches
  - Apply-1 FSM message handler: cancel / invalid input / valid id

The five action button callbacks are thin wrappers around
_run_and_report and are exercised via that helper's underlying
subprocess flow rather than through aiogram dispatching.
"""
import asyncio
import os
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.handlers.admin import migration


# ── _AsyncRecorder ─────────────────────────────────────────────────────

class _AsyncRecorder:
    """Callable async stub that records every invocation."""
    def __init__(self, ret=None):
        self.calls: list = []
        self.ret = ret

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.ret


class _FakeCallback:
    """Minimal aiogram-CallbackQuery stand-in for handler unit tests."""

    def __init__(self, tg_id: int = 1):
        self.from_user = SimpleNamespace(id=tg_id)
        self.message = object()  # opaque — handler must go through safe_edit_text
        self.bot = SimpleNamespace()
        self.bot.send_document = _AsyncRecorder()
        self.answers: list = []

    async def answer(self, text=None, show_alert=False, **kwargs):
        self.answers.append((text, show_alert))


# ── _format_output ─────────────────────────────────────────────────────

class TestFormatOutput:
    def test_includes_header_with_rc_and_title(self):
        out = migration._format_output("title", 0, "hello", "")
        assert "<b>title</b>" in out
        assert "exit code: <code>0</code>" in out
        assert "✅" in out
        assert "hello" in out

    def test_red_icon_for_failure(self):
        out = migration._format_output("t", 1, "", "boom")
        assert "❌" in out
        assert "boom" in out

    def test_warn_icon_for_rc_2(self):
        """Migration script returns 2 when individual rows failed."""
        out = migration._format_output("t", 2, "summary", "")
        assert "⚠️" in out

    def test_escapes_html_in_body(self):
        out = migration._format_output("t", 0, "<script>x</script>", "")
        assert "<script>x</script>" not in out
        assert "&lt;script&gt;" in out

    def test_truncates_long_output_keeps_tail(self):
        long = "X" * (migration._MAX_OUTPUT_CHARS * 2)
        out = migration._format_output("t", 0, long, "")
        assert "truncated" in out
        assert len(out) < len(long) + 500

    def test_empty_outputs_show_placeholder(self):
        out = migration._format_output("t", 0, "", "")
        assert "(empty)" in out


# ── _run_script ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_script_captures_stdout_and_stderr(tmp_path: Path, monkeypatch):
    """Spawn a tiny inline script and check we get rc + stdout + stderr."""
    inline = tmp_path / "echo.py"
    inline.write_text(textwrap.dedent("""
        import sys
        sys.stdout.write("hello from out\\n")
        sys.stderr.write("hello from err\\n")
        sys.exit(7)
    """))
    monkeypatch.setattr(migration, "_SCRIPT_PATH", inline)
    rc, out, err = await migration._run_script([], timeout=10)
    assert rc == 7
    assert "hello from out" in out
    assert "hello from err" in err


@pytest.mark.asyncio
async def test_run_script_times_out_and_kills_process(tmp_path: Path, monkeypatch):
    inline = tmp_path / "hang.py"
    inline.write_text(textwrap.dedent("""
        import time
        time.sleep(60)
    """))
    monkeypatch.setattr(migration, "_SCRIPT_PATH", inline)
    rc, out, err = await migration._run_script([], timeout=1)
    assert rc == 124
    assert "TIMEOUT" in err
    assert out == ""


@pytest.mark.asyncio
async def test_run_script_forwards_args(tmp_path: Path, monkeypatch):
    inline = tmp_path / "argv.py"
    inline.write_text(textwrap.dedent("""
        import sys
        sys.stdout.write("|".join(sys.argv[1:]))
    """))
    monkeypatch.setattr(migration, "_SCRIPT_PATH", inline)
    rc, out, err = await migration._run_script(
        ["--apply", "--telegram-id", "42", "--limit", "1"], timeout=10,
    )
    assert rc == 0
    assert out == "--apply|--telegram-id|42|--limit|1"


@pytest.mark.asyncio
async def test_run_script_preflight_actionable_message_when_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(migration, "_SCRIPT_PATH", tmp_path / "does-not-exist.py")
    rc, out, err = await migration._run_script([], timeout=10)
    assert rc == 127
    assert out == ""
    assert "script not found" in err
    assert ".dockerignore" in err


def test_script_path_constants_point_at_repo():
    assert migration._SCRIPT_PATH.name == "migrate_samopis_to_remnawave.py"
    assert migration._SCRIPT_PATH.parent.name == "scripts"


def test_log_file_default_path_is_tmp_when_env_unset():
    if "MIGRATION_LOG_DIR" not in os.environ:
        assert str(migration._LOG_FILE) == "/tmp/migration_log.csv"
    assert migration._LOG_FILE.name == "migration_log.csv"


# ── _send_csv_if_available ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_csv_skips_when_file_missing(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(migration, "_LOG_FILE", tmp_path / "absent.csv")
    bot = SimpleNamespace(send_document=_AsyncRecorder())
    note = await migration._send_csv_if_available(bot=bot, chat_id=1, caption_prefix="t")
    assert note and "no log file" in note
    assert bot.send_document.calls == []


@pytest.mark.asyncio
async def test_send_csv_refuses_oversized(monkeypatch, tmp_path: Path):
    big = tmp_path / "migration_log.csv"
    big.write_bytes(b"x" * 100)
    monkeypatch.setattr(migration, "_LOG_FILE", big)
    monkeypatch.setattr(migration, "_MAX_DOWNLOAD_BYTES", 10)
    bot = SimpleNamespace(send_document=_AsyncRecorder())
    note = await migration._send_csv_if_available(bot=bot, chat_id=1, caption_prefix="t")
    assert note and "too large" in note
    assert bot.send_document.calls == []


@pytest.mark.asyncio
async def test_send_csv_sends_when_present(monkeypatch, tmp_path: Path):
    csv = tmp_path / "migration_log.csv"
    csv.write_text("ts,tg\n2026,42\n")
    monkeypatch.setattr(migration, "_LOG_FILE", csv)
    bot = SimpleNamespace(send_document=_AsyncRecorder())
    note = await migration._send_csv_if_available(bot=bot, chat_id=12345, caption_prefix="dryrun")
    assert note is None  # no error -> no inline status note
    assert len(bot.send_document.calls) == 1
    args, kwargs = bot.send_document.calls[0]
    assert args[0] == 12345
    from aiogram.types import FSInputFile
    assert isinstance(args[1], FSInputFile)
    assert "dryrun" in kwargs.get("caption", "")


# ── Download handler ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_download_handler_reports_when_no_log_exists(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(migration, "_LOG_FILE", tmp_path / "absent.csv")
    edit_mock = _AsyncRecorder()
    monkeypatch.setattr(migration, "safe_edit_text", edit_mock)
    handler = migration.callback_migration_download.__wrapped__
    cb = _FakeCallback()
    await handler(cb)
    assert cb.bot.send_document.calls == []
    rendered = edit_mock.calls[0][0][1]
    assert "No migration log found" in rendered
    assert "absent.csv" in rendered


@pytest.mark.asyncio
async def test_download_handler_refuses_oversized_file(monkeypatch, tmp_path: Path):
    big = tmp_path / "migration_log.csv"
    big.write_bytes(b"x" * 200)
    monkeypatch.setattr(migration, "_LOG_FILE", big)
    monkeypatch.setattr(migration, "_MAX_DOWNLOAD_BYTES", 100)
    edit_mock = _AsyncRecorder()
    monkeypatch.setattr(migration, "safe_edit_text", edit_mock)
    handler = migration.callback_migration_download.__wrapped__
    cb = _FakeCallback()
    await handler(cb)
    assert cb.bot.send_document.calls == []
    rendered = edit_mock.calls[0][0][1]
    assert "too large" in rendered.lower()


@pytest.mark.asyncio
async def test_download_handler_sends_document_when_file_present(monkeypatch, tmp_path: Path):
    csv_path = tmp_path / "migration_log.csv"
    csv_path.write_text("ts,tg\n2026,42\n", encoding="utf-8")
    monkeypatch.setattr(migration, "_LOG_FILE", csv_path)
    monkeypatch.setattr(migration, "safe_edit_text", _AsyncRecorder())
    handler = migration.callback_migration_download.__wrapped__
    cb = _FakeCallback(tg_id=12345)
    await handler(cb)
    assert len(cb.bot.send_document.calls) == 1
    args, kwargs = cb.bot.send_document.calls[0]
    assert args[0] == 12345
    from aiogram.types import FSInputFile
    assert isinstance(args[1], FSInputFile)


# ── Apply-1 FSM message handler ────────────────────────────────────────

class _FakeFSMContext:
    def __init__(self):
        self.state = None
        self.cleared = False

    async def set_state(self, state):
        self.state = state

    async def clear(self):
        self.cleared = True


class _FakeMessage:
    """Stand-in for incoming aiogram Message; supports `.answer`."""
    def __init__(self, text: str, tg_id: int = 12345):
        self.text = text
        self.from_user = SimpleNamespace(id=tg_id)
        self.bot = SimpleNamespace(send_document=_AsyncRecorder())
        self.answers: list = []
        self.chat = SimpleNamespace(id=tg_id)

    async def answer(self, text: str, **kwargs):
        self.answers.append((text, kwargs))
        # Return a synthetic status msg with the same shape
        status = SimpleNamespace(
            bot=self.bot,
            from_user=SimpleNamespace(id=0),  # bot's id placeholder
        )
        return status


@pytest.mark.asyncio
async def test_apply1_message_handler_cancels_on_keyword():
    handler = migration.message_apply_1_id.__wrapped__
    msg = _FakeMessage("cancel")
    state = _FakeFSMContext()
    await handler(msg, state)
    assert state.cleared is True
    assert any("Cancelled" in t for t, _ in msg.answers)


@pytest.mark.asyncio
async def test_apply1_message_handler_rejects_non_numeric():
    handler = migration.message_apply_1_id.__wrapped__
    msg = _FakeMessage("not a number")
    state = _FakeFSMContext()
    await handler(msg, state)
    # Invalid input must NOT clear state — admin can retry
    assert state.cleared is False
    assert any("Not a valid" in t for t, _ in msg.answers)


@pytest.mark.asyncio
async def test_apply1_message_handler_rejects_negative_id():
    handler = migration.message_apply_1_id.__wrapped__
    msg = _FakeMessage("-1")
    state = _FakeFSMContext()
    await handler(msg, state)
    assert state.cleared is False
    assert any("Not a valid" in t for t, _ in msg.answers)


@pytest.mark.asyncio
async def test_apply1_message_handler_dispatches_subprocess_with_correct_args(
    monkeypatch, tmp_path: Path,
):
    """Valid id → spawns subprocess with --apply --telegram-id N --limit 1."""
    # Stub _run_script to capture the args and short-circuit the spawn.
    captured: list = []

    async def fake_run_script(args, timeout):
        captured.append((list(args), timeout))
        return 0, "Done. ok=1 recovered=0 failed=0", ""

    monkeypatch.setattr(migration, "_run_script", fake_run_script)
    monkeypatch.setattr(migration, "safe_edit_text", _AsyncRecorder())
    monkeypatch.setattr(migration, "_send_csv_if_available", _AsyncRecorder(ret=None))

    handler = migration.message_apply_1_id.__wrapped__
    msg = _FakeMessage("987654321")
    state = _FakeFSMContext()
    await handler(msg, state)

    assert state.cleared is True
    assert len(captured) == 1
    args, _ = captured[0]
    assert args == ["--apply", "--telegram-id", "987654321", "--limit", "1"]


# ── Apply-ALL confirm flow ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_all_first_step_sets_confirm_state(monkeypatch):
    edit_mock = _AsyncRecorder()
    monkeypatch.setattr(migration, "safe_edit_text", edit_mock)
    handler = migration.callback_apply_all_confirm.__wrapped__
    cb = _FakeCallback()
    state = _FakeFSMContext()
    await handler(cb, state)
    assert state.state == migration.AdminMigrationApply.confirm_apply_all
    rendered = edit_mock.calls[0][0][1]
    assert "финальная миграция" in rendered or "ALL" in rendered


@pytest.mark.asyncio
async def test_apply_all_cancel_clears_state(monkeypatch):
    monkeypatch.setattr(migration, "safe_edit_text", _AsyncRecorder())
    handler = migration.callback_apply_all_cancel.__wrapped__
    cb = _FakeCallback()
    state = _FakeFSMContext()
    await handler(cb, state)
    assert state.cleared is True


@pytest.mark.asyncio
async def test_apply_all_yes_runs_with_apply_no_limit(monkeypatch):
    captured: list = []

    async def fake_run_script(args, timeout):
        captured.append((list(args), timeout))
        return 0, "summary", ""

    monkeypatch.setattr(migration, "_run_script", fake_run_script)
    monkeypatch.setattr(migration, "safe_edit_text", _AsyncRecorder())
    monkeypatch.setattr(migration, "_send_csv_if_available", _AsyncRecorder(ret=None))

    handler = migration.callback_apply_all_yes.__wrapped__
    cb = _FakeCallback()
    state = _FakeFSMContext()
    await handler(cb, state)

    assert state.cleared is True
    assert len(captured) == 1
    args, _ = captured[0]
    assert args == ["--apply"]


# ── Apply 500 / Apply 1000 dispatch ───────────────────────────────────

@pytest.mark.asyncio
async def test_apply_500_dispatches_correct_args(monkeypatch):
    captured: list = []

    async def fake_run_script(args, timeout):
        captured.append((list(args), timeout))
        return 0, "summary", ""

    monkeypatch.setattr(migration, "_run_script", fake_run_script)
    monkeypatch.setattr(migration, "safe_edit_text", _AsyncRecorder())
    monkeypatch.setattr(migration, "_send_csv_if_available", _AsyncRecorder(ret=None))

    handler = migration.callback_apply_500.__wrapped__
    cb = _FakeCallback()
    await handler(cb)

    assert len(captured) == 1
    args, timeout = captured[0]
    assert args == ["--apply", "--limit", "500"]
    # Sized for ~50 rows/min × 1.5 safety = 15+ minutes
    assert timeout >= 15 * 60


@pytest.mark.asyncio
async def test_apply_1000_dispatches_correct_args(monkeypatch):
    captured: list = []

    async def fake_run_script(args, timeout):
        captured.append((list(args), timeout))
        return 0, "summary", ""

    monkeypatch.setattr(migration, "_run_script", fake_run_script)
    monkeypatch.setattr(migration, "safe_edit_text", _AsyncRecorder())
    monkeypatch.setattr(migration, "_send_csv_if_available", _AsyncRecorder(ret=None))

    handler = migration.callback_apply_1000.__wrapped__
    cb = _FakeCallback()
    await handler(cb)

    assert len(captured) == 1
    args, timeout = captured[0]
    assert args == ["--apply", "--limit", "1000"]
    assert timeout >= 30 * 60


# ── Migration status ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_handler_renders_progress(monkeypatch, tmp_path: Path):
    """DB returns counts → message contains progress percentage."""
    monkeypatch.setattr(migration, "_LOG_FILE", tmp_path / "absent.csv")
    monkeypatch.setattr(migration, "_LOCK_FILE", tmp_path / "absent.lock")

    # Stub the lazy database import to return canned counts.
    fake_db = SimpleNamespace(
        count_premium_migration_progress=_AsyncRecorder(
            ret={"migrated": 235, "remaining_candidates": 3800, "total_active_paid": 4035}
        ),
    )
    monkeypatch.setitem(sys.modules, "database", fake_db)

    edit_mock = _AsyncRecorder()
    monkeypatch.setattr(migration, "safe_edit_text", edit_mock)

    handler = migration.callback_migration_status.__wrapped__
    cb = _FakeCallback()
    await handler(cb)

    assert len(edit_mock.calls) == 1
    rendered = edit_mock.calls[0][0][1]
    assert "235/4035" in rendered
    assert "3800" in rendered
    assert "5.8%" in rendered  # 235/4035 ≈ 5.82
    assert "no lock file" in rendered
    assert "no log file yet" in rendered


@pytest.mark.asyncio
async def test_status_handler_recognises_held_lock(monkeypatch, tmp_path: Path):
    """Lock file present + alive PID matching marker → 'held by live migration'."""
    lock = tmp_path / "migration.lock"
    lock.write_text(str(os.getpid()))
    monkeypatch.setattr(migration, "_LOCK_FILE", lock)
    monkeypatch.setattr(migration, "_LOG_FILE", tmp_path / "absent.csv")
    # Cmdline of the test process won't contain the real marker — supply
    # a substring that DOES appear in any python invocation.
    monkeypatch.setattr(migration, "_LOCK_CMDLINE_MARKER", "python")

    fake_db = SimpleNamespace(
        count_premium_migration_progress=_AsyncRecorder(
            ret={"migrated": 0, "remaining_candidates": 0, "total_active_paid": 0}
        ),
    )
    monkeypatch.setitem(sys.modules, "database", fake_db)
    edit_mock = _AsyncRecorder()
    monkeypatch.setattr(migration, "safe_edit_text", edit_mock)

    handler = migration.callback_migration_status.__wrapped__
    await handler(_FakeCallback())

    rendered = edit_mock.calls[0][0][1]
    # When marker matches AND PID alive → "held by live migration"
    assert "held by live migration" in rendered or "stale" not in rendered.split("Lock:")[1].split("CSV:")[0]


@pytest.mark.asyncio
async def test_status_handler_reports_pid_reuse_as_stale(monkeypatch, tmp_path: Path):
    """Lock has live PID whose cmdline doesn't match → 'stale (PID … unrelated)'."""
    lock = tmp_path / "migration.lock"
    lock.write_text(str(os.getpid()))
    monkeypatch.setattr(migration, "_LOCK_FILE", lock)
    monkeypatch.setattr(migration, "_LOG_FILE", tmp_path / "absent.csv")
    monkeypatch.setattr(migration, "_LOCK_CMDLINE_MARKER", "this-marker-does-not-exist-anywhere-987654")

    fake_db = SimpleNamespace(
        count_premium_migration_progress=_AsyncRecorder(
            ret={"migrated": 0, "remaining_candidates": 0, "total_active_paid": 0}
        ),
    )
    monkeypatch.setitem(sys.modules, "database", fake_db)
    edit_mock = _AsyncRecorder()
    monkeypatch.setattr(migration, "safe_edit_text", edit_mock)

    handler = migration.callback_migration_status.__wrapped__
    await handler(_FakeCallback())

    rendered = edit_mock.calls[0][0][1]
    assert "stale" in rendered.lower()
    assert "unrelated process" in rendered or "PID reuse" in rendered.lower() or "Clear lock" in rendered


# ── Clear stale lock flow ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clear_lock_prompt_no_lock_clears_state(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(migration, "_LOCK_FILE", tmp_path / "absent.lock")
    monkeypatch.setattr(migration, "safe_edit_text", _AsyncRecorder())
    handler = migration.callback_clear_lock_prompt.__wrapped__
    cb = _FakeCallback()
    state = _FakeFSMContext()
    await handler(cb, state)
    # No lock to confirm against → state cleared, no FSM hold-up.
    assert state.cleared is True


@pytest.mark.asyncio
async def test_clear_lock_prompt_warns_when_holder_alive(monkeypatch, tmp_path: Path):
    lock = tmp_path / "migration.lock"
    lock.write_text(str(os.getpid()))
    monkeypatch.setattr(migration, "_LOCK_FILE", lock)
    monkeypatch.setattr(migration, "_LOCK_CMDLINE_MARKER", "python")  # matches test runner
    edit_mock = _AsyncRecorder()
    monkeypatch.setattr(migration, "safe_edit_text", edit_mock)

    handler = migration.callback_clear_lock_prompt.__wrapped__
    cb = _FakeCallback()
    state = _FakeFSMContext()
    await handler(cb, state)
    rendered = edit_mock.calls[0][0][1]
    assert "WARNING" in rendered
    assert state.state == migration.AdminMigrationApply.confirm_clear_lock


@pytest.mark.asyncio
async def test_clear_lock_yes_unlinks_file(monkeypatch, tmp_path: Path):
    lock = tmp_path / "migration.lock"
    lock.write_text("31")
    monkeypatch.setattr(migration, "_LOCK_FILE", lock)
    monkeypatch.setattr(migration, "safe_edit_text", _AsyncRecorder())

    handler = migration.callback_clear_lock_confirm.__wrapped__
    cb = _FakeCallback()
    state = _FakeFSMContext()
    await handler(cb, state)
    assert not lock.exists()
    assert state.cleared is True


@pytest.mark.asyncio
async def test_clear_lock_no_keeps_file(monkeypatch, tmp_path: Path):
    lock = tmp_path / "migration.lock"
    lock.write_text("31")
    monkeypatch.setattr(migration, "_LOCK_FILE", lock)
    monkeypatch.setattr(migration, "safe_edit_text", _AsyncRecorder())

    handler = migration.callback_clear_lock_cancel.__wrapped__
    cb = _FakeCallback()
    state = _FakeFSMContext()
    await handler(cb, state)
    assert lock.exists()  # untouched on cancel
    assert lock.read_text() == "31"
    assert state.cleared is True


# ── Lock-state introspection ──────────────────────────────────────────

def test_read_lock_state_no_file(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(migration, "_LOCK_FILE", tmp_path / "absent.lock")
    out = migration._read_lock_state()
    assert out["present"] is False
    assert out["pid"] is None
    assert out["alive"] is False


def test_read_lock_state_malformed_file(monkeypatch, tmp_path: Path):
    lock = tmp_path / "migration.lock"
    lock.write_text("not-an-int")
    monkeypatch.setattr(migration, "_LOCK_FILE", lock)
    out = migration._read_lock_state()
    assert out["present"] is True
    assert out["pid"] is None
    assert out["alive"] is False


def test_read_lock_state_dead_pid(monkeypatch, tmp_path: Path):
    lock = tmp_path / "migration.lock"
    lock.write_text("2147483646")  # almost certainly dead
    monkeypatch.setattr(migration, "_LOCK_FILE", lock)
    out = migration._read_lock_state()
    assert out["present"] is True
    assert out["pid"] == 2147483646
    assert out["alive"] is False


def test_read_lock_state_pid_reused_by_unrelated_process(monkeypatch, tmp_path: Path):
    """Live PID + cmdline doesn't match marker → alive=True, our_script=False."""
    lock = tmp_path / "migration.lock"
    lock.write_text(str(os.getpid()))
    monkeypatch.setattr(migration, "_LOCK_FILE", lock)
    monkeypatch.setattr(migration, "_LOCK_CMDLINE_MARKER", "no-such-marker-xyz-987")
    out = migration._read_lock_state()
    assert out["present"] is True
    assert out["pid"] == os.getpid()
    assert out["alive"] is True
    assert out["our_script"] is False


# ── CSV summary ───────────────────────────────────────────────────────

def test_read_csv_summary_no_file(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(migration, "_LOG_FILE", tmp_path / "absent.csv")
    out = migration._read_csv_summary()
    assert out["present"] is False
    assert out["rows"] == 0


def test_read_csv_summary_counts_data_rows_excluding_header(monkeypatch, tmp_path: Path):
    csv = tmp_path / "migration_log.csv"
    csv.write_text("ts,tg,uuid\n2026,1,a\n2026,2,b\n2026,3,c\n")
    monkeypatch.setattr(migration, "_LOG_FILE", csv)
    out = migration._read_csv_summary()
    assert out["present"] is True
    assert out["rows"] == 3
    assert "2026,3,c" in out["last_line"]
