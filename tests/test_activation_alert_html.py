"""The "activation failed" admin alert was sent with parse_mode="Markdown"
(legacy). A Combo tariff (`combo_basic`) or a panel error with `_`, `*`, `[`
or a backtick made Telegram reject it ("can't parse entities");
safe_send_message swallowed the 400 and the admin never learned that a paid
subscription could not be activated. It is now HTML with escaped values.
"""
from __future__ import annotations

import ast
from pathlib import Path

from app.utils.telegram_html import TEXT_LIMIT, telegram_html_errors, visible_length

ROOT = Path(__file__).resolve().parents[1]
_SKIP = {".venv", "venv", "tests", "node_modules", "scripts", "dashboard", "docs", ".claude", ".git"}


def test_no_markdown_parse_mode_in_production_code():
    """Legacy Markdown breaks on any `_*[` in user or panel data; the bot is HTML-only."""
    found = []
    for path in ROOT.rglob("*.py"):
        if _SKIP & set(path.relative_to(ROOT).parts):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.keyword) and node.arg == "parse_mode" and isinstance(node.value, ast.Constant) \
                    and str(node.value.value).lower().startswith("markdown"):
                found.append(f"{path.relative_to(ROOT)}:{node.value.lineno}")
    assert found == []


def test_activation_failed_alert_is_valid_html_with_hostile_values():
    from activation_worker import build_activation_failed_alert

    text = build_activation_failed_alert(
        subscription_id=42,
        telegram_id=9_999_999_999,
        subscription_type="combo_basic",
        amount_rubles=1490,
        period_days=30,
        attempts=5,
        max_attempts=5,
        error_msg="Remnawave 400: <html> user_name `x` [a](b) *bold* & more " + "x" * 5000,
    )
    assert telegram_html_errors(text) == []
    assert visible_length(text) <= TEXT_LIMIT
    assert "combo_basic" in text
    assert "&lt;html&gt;" in text
    assert "<b>" in text and "`" not in text.split("Remnawave")[0]
