"""Share texts leave the bot as plain text; user data inside HTML is escaped.

Regression for the owner's bug report: the gift «Отправить» share button
pre-filled the user's compose box with raw `<tg-emoji emoji-id=…>` markup,
because t.me/share/url text is never parsed as HTML.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlparse

import pytest

from app.handlers.callbacks import gift
from app.handlers.user import referrals
from app.handlers.common.screens import _profile_header
from app.i18n import get_text


def _share_button_url(markup) -> str:
    urls = [b.url for row in markup.inline_keyboard for b in row if b.url]
    share = [u for u in urls if u.startswith("https://t.me/share/url?")]
    assert len(share) == 1, urls
    return share[0]


def _assert_clean_share(url: str, gift_link: str | None = None) -> str:
    raw_query = urlparse(url).query
    # properly URL-encoded: no raw markup, spaces or newlines in the query
    for ch in ("<", ">", " ", "\n", '"'):
        assert ch not in raw_query, (ch, raw_query)
    q = parse_qs(raw_query)
    if gift_link:
        assert q["url"] == [gift_link]
    text = q["text"][0]
    assert "<" not in text and "tg-emoji" not in text and "&lt;" not in text, text
    return text


def _fake_bot(username="AtlasBot"):
    bot = MagicMock()
    bot.get_me = AsyncMock(return_value=SimpleNamespace(username=username))
    bot.send_message = AsyncMock()
    return bot


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_gift_share_text_i18n_is_plain(lang):
    text = get_text(lang, "gift.share_text", tariff_name="Plus", period="1 месяц")
    assert "<" not in text and ">" not in text
    assert text.startswith("🎁")


@pytest.mark.parametrize("lang", ["ru", "en"])
async def test_gift_success_share_url_is_plain_and_encoded(lang):
    bot = _fake_bot()
    await gift._send_gift_success(bot, 42, lang, "CODE123", "plus", 30)

    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["parse_mode"] == "HTML"
    gift_link = "https://t.me/AtlasBot?start=gift_CODE123"
    text = _assert_clean_share(_share_button_url(kwargs["reply_markup"]), gift_link)
    # the period in the user's language (08 #21: EN used to get «1 месяц»)
    assert "Plus" in text and {"ru": "1 месяц", "en": "1 month"}[lang] in text
    assert text.startswith("🎁")


async def test_gift_detail_share_url_is_plain_and_encoded(monkeypatch):
    monkeypatch.setattr(gift, "ensure_db_ready_callback", AsyncMock(return_value=True))
    monkeypatch.setattr(gift, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(
        gift.database,
        "get_user_gifts",
        AsyncMock(return_value=[
            {"id": 5, "tariff": "combo_plus", "period_days": 90, "gift_code": "ZZ9", "status": "pending"},
        ]),
    )
    edit = AsyncMock()
    monkeypatch.setattr(gift, "safe_edit_text", edit)

    callback = MagicMock()
    callback.answer = AsyncMock()
    callback.from_user.id = 42
    callback.data = "gift_detail:5:0"
    callback.bot = _fake_bot()

    await gift.callback_gift_detail(callback)

    edit.assert_awaited_once()
    markup = edit.await_args.kwargs["reply_markup"]
    text = _assert_clean_share(_share_button_url(markup), "https://t.me/AtlasBot?start=gift_ZZ9")
    assert "Комбо Plus" in text and "3 месяца" in text


async def test_share_discount_share_url_is_plain_and_encoded(monkeypatch):
    monkeypatch.setattr(referrals, "resolve_user_language", AsyncMock(return_value="en"))
    monkeypatch.setattr(
        referrals, "build_share_discount_link",
        AsyncMock(return_value="https://t.me/AtlasBot?start=refd_abc&x=1"),
    )
    callback = MagicMock()
    callback.answer = AsyncMock()
    callback.from_user.id = 7
    callback.bot = _fake_bot()
    callback.message.answer = AsyncMock()

    await referrals.callback_share_discount_open(callback)

    markup = callback.message.answer.await_args.kwargs["reply_markup"]
    _assert_clean_share(_share_button_url(markup), "https://t.me/AtlasBot?start=refd_abc&x=1")


def test_profile_header_escapes_user_name():
    header = _profile_header("<3 Tom & Jerry</b>", 123)
    assert header.startswith("👤 <b>&lt;3 Tom &amp; Jerry&lt;/b&gt;</b>\n")
    assert "<code>123</code>" in header
