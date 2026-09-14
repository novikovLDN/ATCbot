"""A trial attempt that activated nothing gives its token back (production
2026-09-14: a job_exists refusal consumed the 1/hour trial limit and every
next click was answered «Слишком много запросов» for an hour)."""
from app.core.rate_limit import RateLimiter


def test_refund_lets_the_next_attempt_through():
    rl = RateLimiter()
    assert rl.check_rate_limit(1, "trial_activate")[0] is True
    assert rl.check_rate_limit(1, "trial_activate")[0] is False
    rl.refund(1, "trial_activate")
    assert rl.check_rate_limit(1, "trial_activate")[0] is True


def test_reset_user_clears_only_that_users_limits():
    rl = RateLimiter()
    assert rl.check_rate_limit(3, "trial_activate")[0] is True
    assert rl.check_rate_limit(4, "trial_activate")[0] is True
    rl.reset_user(3)
    assert rl.check_rate_limit(3, "trial_activate")[0] is True     # fresh
    assert rl.check_rate_limit(4, "trial_activate")[0] is False    # untouched


def test_refund_never_exceeds_the_limit_and_ignores_unknown_keys():
    rl = RateLimiter()
    rl.refund(2, "trial_activate")                      # no bucket yet: nothing happens
    assert rl.check_rate_limit(2, "trial_activate")[0] is True
    rl.refund(2, "trial_activate")
    rl.refund(2, "trial_activate")                      # capped at max_requests (1)
    assert rl.check_rate_limit(2, "trial_activate")[0] is True
    assert rl.check_rate_limit(2, "trial_activate")[0] is False
