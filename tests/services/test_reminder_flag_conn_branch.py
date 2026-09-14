"""P2 (latent): notifications.service marking a reminder as sent on a caller's
connection read database._REMINDER_FLAG_UPDATE_QUERIES /
database._ALLOWED_REMINDER_FLAGS, which the database package does not
re-export (they live in database.subscriptions) → AttributeError on the
`conn is not None` branch. No caller passes conn today; the branch must still
work when one does.
"""
import pytest

from app.services.notifications import service as ns
from database.subscriptions import _REMINDER_FLAG_UPDATE_QUERIES


class _Conn:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return "UPDATE 1"


async def test_mark_reminder_sent_on_a_callers_connection():
    flag, query = next(iter(_REMINDER_FLAG_UPDATE_QUERIES.items()))
    reminder_type = next(
        (rt for rt in ns.ReminderType if ns.get_reminder_flag_name(rt) == flag), None,
    )
    if reminder_type is None:
        pytest.skip("no reminder type maps to the first flag")
    conn = _Conn()
    await ns.mark_reminder_sent(12345, reminder_type, conn=conn)
    assert conn.calls == [(query, (12345,))]
