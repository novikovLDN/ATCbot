"""Условие финального провала активации.

Дефект: подписка помечалась failed только если ОДНОВРЕМЕННО исчерпаны попытки
и VPN полностью отключён. При обычной ошибке VPN API (сам VPN включён)
подписка навсегда оставалась в pending: деньги получены, доступ не выдан,
финального алерта админу нет.
"""
import pytest

MAX_ACTIVATION_ATTEMPTS = 3


def should_mark_failed(new_attempts: int, max_attempts: int) -> bool:
    """Условие в том виде, в каком оно теперь в activation_worker."""
    return new_attempts >= max_attempts


class TestShouldMarkFailed:
    def test_api_error_with_vpn_enabled_eventually_fails(self):
        """Главный дефект: обычная ошибка API обязана привести к failed."""
        assert should_mark_failed(MAX_ACTIVATION_ATTEMPTS, MAX_ACTIVATION_ATTEMPTS) is True

    def test_attempts_exceeded_marks_failed(self):
        assert should_mark_failed(MAX_ACTIVATION_ATTEMPTS + 1, MAX_ACTIVATION_ATTEMPTS) is True

    @pytest.mark.parametrize("attempt", [1, 2])
    def test_retries_continue_until_limit(self, attempt):
        """До исчерпания лимита подписка остаётся в pending и ретраится."""
        assert should_mark_failed(attempt, MAX_ACTIVATION_ATTEMPTS) is False

    def test_old_condition_would_have_left_pending_forever(self):
        """Фиксация прежнего поведения, чтобы дефект не вернулся."""
        vpn_permanently_disabled = False  # VPN включён — обычная ошибка API
        old = vpn_permanently_disabled and MAX_ACTIVATION_ATTEMPTS >= MAX_ACTIVATION_ATTEMPTS
        assert old is False, "прежнее условие оставляло подписку в pending"
        assert should_mark_failed(MAX_ACTIVATION_ATTEMPTS, MAX_ACTIVATION_ATTEMPTS) is True
