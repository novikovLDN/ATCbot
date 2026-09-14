"""P2: device names from the HWID headers (deviceModel / platform / osVersion,
set by the VPN client, i.e. user-controlled) were put into HTML messages
unescaped. A model like "Pixel <8>" or "R&D box" → Telegram "can't parse
entities" → the Devices screen and the delete confirmation never rendered.
"""
from app.handlers.user import devices


def test_device_label_is_html_escaped():
    label = devices._device_label({"deviceModel": "Pixel <8> & Co", "osVersion": "14<b>"})
    assert "<" not in label and ">" not in label
    assert "&lt;8&gt;" in label and "&amp;" in label and "14&lt;b&gt;" in label


def test_plain_label_unchanged():
    assert devices._device_label({"deviceModel": "iPhone 15", "osVersion": "17.5"}) == "iPhone 15 · 17.5"
    assert devices._device_label({}) == "Устройство"
