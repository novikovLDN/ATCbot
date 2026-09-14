"""html_to_plain / build_share_url — texts that leave the bot's HTML messages."""
from urllib.parse import parse_qs, urlparse

from app.utils.text_format import build_share_url, html_to_plain

GIFT_EMOJI = '<tg-emoji emoji-id="5449800250032143374">🎁</tg-emoji>'


def test_empty_and_none():
    assert html_to_plain("") == ""
    assert html_to_plain(None) == ""


def test_plain_text_is_untouched():
    assert html_to_plain("🎁 Привет! Дарю подписку.") == "🎁 Привет! Дарю подписку."


def test_tg_emoji_becomes_fallback_emoji():
    assert html_to_plain(f"{GIFT_EMOJI} Привет!") == "🎁 Привет!"


def test_tg_emoji_single_quotes_and_multiple():
    text = "<tg-emoji emoji-id='1'>⚡️</tg-emoji> a <tg-emoji emoji-id=\"2\">🍎</tg-emoji> b"
    assert html_to_plain(text) == "⚡️ a 🍎 b"


def test_simple_tags_are_stripped_content_kept():
    assert html_to_plain("<b>Жирный</b> <i>курсив</i> <u>u</u> <s>s</s>") == "Жирный курсив u s"
    assert html_to_plain("Код: <code>ABC-123</code>") == "Код: ABC-123"
    assert html_to_plain('<a href="https://t.me/x">ссылка</a>') == "ссылка"
    assert html_to_plain("<tg-spoiler>секрет</tg-spoiler>") == "секрет"


def test_nested_tags():
    text = f"<b>{GIFT_EMOJI} <i>Подарок — <code>Plus</code></i></b>"
    assert html_to_plain(text) == "🎁 Подарок — Plus"


def test_blockquote_goes_on_its_own_paragraph():
    text = "Текст<blockquote>⚠️ Ссылка работает 1 раз</blockquote>Хвост"
    assert html_to_plain(text) == "Текст\n\n⚠️ Ссылка работает 1 раз\n\nХвост"


def test_blockquote_expandable_and_trailing():
    text = "<b>Твоя ссылка:</b>\n<blockquote expandable><code>https://t.me/b?start=x</code></blockquote>"
    assert html_to_plain(text) == "Твоя ссылка:\n\nhttps://t.me/b?start=x"


def test_entities_are_unescaped_after_tag_strip():
    assert html_to_plain("Tom &amp; Jerry &lt;3 &quot;q&quot; &#39;a&#39;") == "Tom & Jerry <3 \"q\" 'a'"
    # escaped markup is literal text, not a tag to strip
    assert html_to_plain("&lt;b&gt;not bold&lt;/b&gt;") == "<b>not bold</b>"


def test_newlines_normalized():
    text = "a  \n\n\n\n<b>b</b>\t\n<br>c<br/>d"
    assert html_to_plain(text) == "a\n\nb\n\nc\nd"


def test_comparison_signs_that_are_not_tags_survive():
    assert html_to_plain("5 < 7 and 9 > 3") == "5 < 7 and 9 > 3"


def test_real_gift_i18n_strings_render_plain():
    from app.i18n import get_text

    for lang in ("ru", "en"):
        for key in ("gift.success", "gift.detail_pending", "gift.intro"):
            plain = html_to_plain(
                get_text(lang, key, tariff_name="Plus", period="1 месяц", gift_link="https://t.me/b?start=gift_x")
            )
            assert "<" not in plain and "tg-emoji" not in plain, (lang, key, plain)


def test_build_share_url_encodes_and_plains():
    url = build_share_url("https://t.me/bot?start=gift_a&b", f"{GIFT_EMOJI} <b>Hi</b> & bye\nline2")
    assert url.startswith("https://t.me/share/url?url=")
    raw_query = urlparse(url).query
    assert "<" not in raw_query and ">" not in raw_query and " " not in raw_query and "\n" not in raw_query
    q = parse_qs(raw_query)
    assert q["url"] == ["https://t.me/bot?start=gift_a&b"]
    assert q["text"] == ["🎁 Hi & bye\nline2"]


def test_build_share_url_without_text():
    assert build_share_url("https://t.me/bot?start=r") == "https://t.me/share/url?url=https%3A%2F%2Ft.me%2Fbot%3Fstart%3Dr"
    assert "text=" not in build_share_url("https://t.me/x", "<b></b>")
