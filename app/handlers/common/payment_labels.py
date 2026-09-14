"""Payment-method buttons shared by the purchase / top-up screens
(docs/audit/08_payments_ux.md #12, #14, #20):

  * SBP shows the REAL markup (SBP_MARKUP_PERCENT) or nothing — EN used to
    say «+11%» while the markup was 0, RU never showed a real one;
  * one button per cash desk: with WATA on, «Банковская карта» led to the same
    WATA page as the WATA button — only the WATA button («Карта / СБП») is shown;
  * the preset and the custom top-up amount get the same method list.
"""
from __future__ import annotations

import config
from aiogram.types import InlineKeyboardButton

from app.handlers.common.emoji import CE
from app.i18n import get_text as i18n_get_text


def sbp_label(language: str) -> str:
    pct = int(getattr(config, "SBP_MARKUP_PERCENT", 0) or 0)
    base = i18n_get_text(language, "payment.sbp")
    return base + (i18n_get_text(language, "payment.markup_suffix", percent=pct) if pct > 0 else "")


def topup_method_rows(language: str, amount: int) -> list:
    """Top-up method buttons for `amount` ₽ (preset and custom amount alike)."""
    import platega_service
    import wata_service

    rows = []
    if wata_service.is_enabled():
        rows.append([InlineKeyboardButton(
            text=i18n_get_text(language, "payment.lava"),
            callback_data=f"topup_wata:{amount}",
            icon_custom_emoji_id=CE["buy"],
            style="success",
        )])
    else:
        rows.append([InlineKeyboardButton(
            text=i18n_get_text(language, "main.pay_with_card"),
            callback_data=f"topup_card:{amount}",
            icon_custom_emoji_id=CE["buy"],
            style="success",
        )])
    if platega_service.is_enabled():
        rows.append([InlineKeyboardButton(
            text=sbp_label(language),
            callback_data=f"topup_sbp:{amount}",
            style="primary",
        )])
    rows.append([InlineKeyboardButton(
        text=i18n_get_text(language, "payment.stars"),
        callback_data=f"topup_stars:{amount}",
        style="primary",
    )])
    return rows
