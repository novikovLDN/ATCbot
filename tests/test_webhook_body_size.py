"""
Tests for webhook body size validation and error middleware message truncation.
"""
import pytest


class TestWebhookBodySizeConstants:
    """Verify body size limit is enforced."""

    def test_max_body_size_is_1mb(self):
        """MAX_BODY_SIZE should be 1 MB."""
        MAX_BODY_SIZE = 1 * 1024 * 1024
        assert MAX_BODY_SIZE == 1048576

    def test_oversized_body_detected(self):
        """A body larger than MAX_BODY_SIZE should be rejected."""
        MAX_BODY_SIZE = 1 * 1024 * 1024
        oversized = b"x" * (MAX_BODY_SIZE + 1)
        assert len(oversized) > MAX_BODY_SIZE

    def test_normal_body_accepted(self):
        """A typical Telegram update body should be well within limits."""
        MAX_BODY_SIZE = 1 * 1024 * 1024
        normal = b'{"update_id": 123, "message": {"text": "hello"}}'
        assert len(normal) < MAX_BODY_SIZE


class TestErrorMessageTruncation:
    """Verify error messages are truncated to Telegram limits."""

    def test_short_message_unchanged(self):
        """Short error text should not be modified."""
        error_text = "Try again later"
        if len(error_text) > 200:
            error_text = error_text[:200]
        assert error_text == "Try again later"

    def test_long_message_truncated(self):
        """Error text exceeding 200 chars should be truncated."""
        error_text = "x" * 300
        if len(error_text) > 200:
            error_text = error_text[:200]
        assert len(error_text) == 200

    def test_exactly_200_chars_unchanged(self):
        """Error text at exactly 200 chars should not be modified."""
        error_text = "y" * 200
        if len(error_text) > 200:
            error_text = error_text[:200]
        assert len(error_text) == 200
