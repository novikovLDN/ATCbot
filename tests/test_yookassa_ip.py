"""
Tests for YooKassa webhook IP verification.
"""
import pytest


class TestVerifyWebhookIp:
    """YooKassa IP allowlist verification tests."""

    def _verify(self, ip: str) -> bool:
        from yookassa_service import verify_webhook_ip
        return verify_webhook_ip(ip)

    def test_valid_yookassa_ip_range1(self):
        assert self._verify("185.71.76.1") is True

    def test_valid_yookassa_ip_range2(self):
        assert self._verify("185.71.77.15") is True

    def test_valid_yookassa_ip_range3(self):
        assert self._verify("77.75.153.100") is True

    def test_valid_yookassa_ip_exact1(self):
        assert self._verify("77.75.156.11") is True

    def test_valid_yookassa_ip_exact2(self):
        assert self._verify("77.75.156.35") is True

    def test_valid_yookassa_ip_range4(self):
        assert self._verify("77.75.154.200") is True

    def test_invalid_ip_rejected(self):
        assert self._verify("8.8.8.8") is False

    def test_private_ip_rejected(self):
        assert self._verify("192.168.1.1") is False

    def test_empty_ip_allowed_with_fallback(self):
        """Empty IP allowed because API re-fetch is the primary verification."""
        assert self._verify("") is True

    def test_invalid_format_allowed_with_fallback(self):
        """Invalid IP format allowed because API re-fetch protects."""
        assert self._verify("not-an-ip") is True

    def test_localhost_rejected(self):
        assert self._verify("127.0.0.1") is False
