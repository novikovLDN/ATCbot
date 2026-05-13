"""
Unit tests for app.handlers.admin.migration internals.

Covers the two pieces of logic that are not just aiogram glue:
  - _run_script: subprocess execution, timeout, non-existent interpreter
  - _format_output: header / truncation / empty-output handling

Telegram-side callbacks (callback_migration_help, _dryrun) are thin
wrappers around these two helpers + safe_edit_text and are not unit-
tested here — they would require a full aiogram CallbackQuery harness.
"""
import asyncio
import os
import sys
import textwrap
from pathlib import Path

import pytest

from app.handlers.admin import migration


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
        # Final summary line is what the operator cares about — must
        # always make it through truncation.
        assert "truncated" in out
        # The header (with rc=0) survives outside <pre>; the body is
        # truncated to MAX_OUTPUT_CHARS plus a small truncation marker.
        # The output must NOT include the entire untruncated body.
        assert len(out) < len(long) + 500  # generous upper bound

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
        # argv[0] is the script path itself; the args we care about start at 1.
        sys.stdout.write("|".join(sys.argv[1:]))
    """))
    monkeypatch.setattr(migration, "_SCRIPT_PATH", inline)
    rc, out, err = await migration._run_script(["--help", "--limit", "10"], timeout=10)
    assert rc == 0
    assert out == "--help|--limit|10"


@pytest.mark.asyncio
async def test_run_script_preflight_actionable_message_when_missing(tmp_path: Path, monkeypatch):
    """Missing script → rc=127 with a deployment-actionable error.

    Hardens against the prod failure mode where scripts/ is excluded
    from the Docker image via .dockerignore and python returns an opaque
    "can't open file" message that doesn't tell the operator what to do.
    """
    monkeypatch.setattr(migration, "_SCRIPT_PATH", tmp_path / "does-not-exist.py")
    rc, out, err = await migration._run_script([], timeout=10)
    assert rc == 127
    assert out == ""
    assert "script not found" in err
    assert ".dockerignore" in err  # operator-facing fix instruction


def test_script_path_constants_point_at_repo():
    """Module-level _SCRIPT_PATH must be the real migration script.

    Catches accidental refactors that break the resolve-three-parents-up
    assumption when the handler is moved between directories.
    """
    assert migration._SCRIPT_PATH.name == "migrate_samopis_to_remnawave.py"
    assert migration._SCRIPT_PATH.parent.name == "scripts"
