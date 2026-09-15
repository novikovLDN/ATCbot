"""httpx lines for successful per-user panel reads (traffic_monitor polls every
active user each pass) are dropped; everything else httpx logs is kept."""
import logging

import pytest

from app.core.logging_config import PanelUserPollFilter


def _rec(msg, *args, level=logging.INFO):
    return logging.LogRecord("httpx", level, __file__, 1, msg, args, None)


HTTPX_FMT = 'HTTP Request: %s %s "%s %d %s"'


@pytest.mark.parametrize("method,url,code,phrase,kept", [
    ("GET", "https://rmnw.atlassecure.ru/api/users/42135", 200, "OK", False),
    ("GET", "https://rmnw.atlassecure.ru/api/users/63516", 404, "Not Found", True),
    ("GET", "https://rmnw.atlassecure.ru/api/users/42135", 500, "Internal Server Error", True),
    ("POST", "https://rmnw.atlassecure.ru/api/users", 201, "Created", True),
    ("POST", "https://rmnw.atlassecure.ru/api/users/resolve", 404, "Not Found", True),
    ("GET", "https://rmnw.atlassecure.ru/api/users/7f59c694-aaaa-bbbb-cccc-000000000000", 200, "OK", True),
    ("GET", "https://api.wata.pro/api/h2h/v2/transactions/?orderId=purchase_1", 200, "OK", True),
])
def test_only_successful_numeric_user_reads_are_dropped(method, url, code, phrase, kept):
    rec = _rec(HTTPX_FMT, method, url, "HTTP/1.1", code, phrase)
    assert PanelUserPollFilter().filter(rec) is kept


def test_warnings_are_never_dropped():
    rec = _rec(HTTPX_FMT, "GET", "https://rmnw.atlassecure.ru/api/users/1", "HTTP/1.1", 200, "OK",
               level=logging.WARNING)
    assert PanelUserPollFilter().filter(rec) is True
