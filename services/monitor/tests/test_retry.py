"""A poll whose upstream was down retries after RETRY_DOWN_S, not the full interval (agri's is 6 h)."""
import asyncio

import pytest

from monitor import engine as E
from monitor import sources


@pytest.mark.parametrize("state,expected", [("down", E.RETRY_DOWN_S), ("ok", 21600)])
def test_every_retries_soon_when_dep_down(monkeypatch, state, expected):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 2:              # first_delay, then the post-run sleep
            raise asyncio.CancelledError

    async def poll():
        sources.DEPS["agri"] = state

    monkeypatch.setattr(E.asyncio, "sleep", fake_sleep)
    eng = E.Engine.__new__(E.Engine)
    eng.last_run = {}
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(eng._every("agri", 21600, poll, 5, dep="agri"))
    assert sleeps == [5, expected]
