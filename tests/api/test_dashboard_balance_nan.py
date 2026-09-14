"""P2: POST /users/{id}/balance accepted delta_rubles=NaN (pydantic allows
NaN floats by default and both validator checks are False for NaN): it ended
in round(nan * 100) → ValueError → 500 with the exception text. Now 422.
"""
import math

import pytest
from pydantic import ValidationError

from app.api.dashboard.routes.users import BalanceRequest


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_delta_is_rejected(bad):
    with pytest.raises(ValidationError):
        BalanceRequest(delta_rubles=bad)


def test_finite_delta_ok():
    assert BalanceRequest(delta_rubles=-12.5).delta_rubles == -12.5
