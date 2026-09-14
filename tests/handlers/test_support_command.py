"""P2: /support is in the bot's command menu (main.py set_my_commands) but
had no handler — the catch-all ignored it silently, tapping it did nothing.
It now opens the same help screen as /help (FAQ / instructions / operator).
"""
from aiogram.filters import Command

from app.handlers.user.support import user_router


def _commands_of(handler):
    out = set()
    for f in handler.filters or []:
        cb = getattr(f, "callback", None)
        if isinstance(cb, Command):
            out |= {c if isinstance(c, str) else getattr(c, "pattern", str(c)) for c in cb.commands}
    return out


def test_support_command_has_a_handler():
    handled = set()
    for h in user_router.message.handlers:
        handled |= _commands_of(h)
    assert "support" in handled
    assert "help" in handled
