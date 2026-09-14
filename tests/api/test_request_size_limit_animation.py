"""P2: the dashboard GIF/MP4 broadcast upload (handler allows 20 MB) was cut
at the 1 MB global body limit — RequestSizeLimitMiddleware only had an
override for upload-photo, so any animation over 1 MB got a 413 before it
reached the handler.
"""
from app.api import RequestSizeLimitMiddleware

MB = 1024 * 1024


def _limit(path):
    return RequestSizeLimitMiddleware(app=None)._limit_for(path)


def test_animation_upload_gets_20mb():
    assert _limit("/dashboard/api/broadcasts/upload-animation") == 20 * MB


def test_other_limits_unchanged():
    assert _limit("/dashboard/api/broadcasts/upload-photo") == 10 * MB
    assert _limit("/webhooks/wata") == 1 * MB
    assert _limit("/dashboard/api/users/1/balance") == 1 * MB
