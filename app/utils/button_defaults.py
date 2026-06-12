"""
Global default `style="danger"` for every InlineKeyboardButton in the
bot — Bot API 9.4 button color.

How it works:
    `InlineKeyboardButton` is a Pydantic v2 model. Pydantic builds field
    descriptors at class-creation time, so changing the field's default
    afterwards has no effect. Instead, we wrap the class's `__init__`:
    if `style` wasn't explicitly passed (or was passed as None), we
    inject `"danger"`. Anything explicit — `style="primary"`,
    `style="success"`, even `style=None` if a caller really wants no
    style — is respected.

When to import:
    Once, as early as possible — BEFORE the handler modules load. The
    canonical place is the top of `main.py`, right after `setup_logging()`.
    Why: handler modules do `from aiogram.types import InlineKeyboardButton`,
    which captures a reference to the class object. Since we mutate the
    SAME class object (not a re-binding in aiogram.types), all those
    references see the patched `__init__` regardless of import order —
    but importing once at startup makes the patch idempotent and easy
    to trace in tracebacks.

Reverting:
    Override the style on a specific button by passing `style=...`
    explicitly. To turn the whole effect off, delete the import in
    `main.py`.
"""

from aiogram.types import InlineKeyboardButton

_DEFAULT_STYLE = "danger"
_original_init = InlineKeyboardButton.__init__


def _danger_default_init(self, **kwargs):
    # `setdefault` would also work, but be explicit about the None case:
    # a caller passing `style=None` clearly wants no style — keep it.
    if "style" not in kwargs:
        kwargs["style"] = _DEFAULT_STYLE
    _original_init(self, **kwargs)


# Idempotent: re-import doesn't double-wrap. Marker attr on the class
# ensures we only patch once even if this module is reloaded.
if not getattr(InlineKeyboardButton, "_atlas_danger_patched", False):
    InlineKeyboardButton.__init__ = _danger_default_init
    InlineKeyboardButton._atlas_danger_patched = True
