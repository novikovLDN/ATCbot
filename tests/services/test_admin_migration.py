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
