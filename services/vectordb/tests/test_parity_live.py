"""Weaviate vs numpy parity (Phase 5): both backends must return the same analogs, in the same order, with and
without the backtest holdout. Needs a running, seeded Weaviate and the real bge-small model; skipped otherwise."""
from __future__ import annotations

import asyncio

import pytest

from copilot_common import reachability
from vectordb import client as wv
from vectordb.analogs import AnalogReq, find_analogs, numpy_search, weaviate_search
from vectordb.corpus import load_events

QUERIES = [("Severe cyclone making landfall in Odisha", "cyclone", "Odisha"),
           ("OPEC cuts output, crude oil spikes 10%", "oil", None),
           ("RBI raises the repo rate by 50 bps", "rates", None),
           ("Monsoon rainfall deficit hits kharif sowing", "monsoon", None),
           ("Hurricane in the Gulf of Mexico shuts refineries", "hurricane", "Gulf"),
           ("Heatwave across north India, power demand record", "heatwave", None),
           ("Government bans sugar exports", "policy", None),
           ("Flooding in Kerala", None, None)]


@pytest.fixture(autouse=True)
def weaviate_breaker_open():
    """Overrides conftest's fixture of the same name: these tests talk to the real Weaviate."""
    reachability.mark_up(wv.weaviate_url())
    yield
    reachability.mark_down(wv.weaviate_url(), ttl_s=3600)


@pytest.fixture(scope="module")
def live(monkeypatch_module):
    from copilot_common.settings import get_settings
    if get_settings().MOCK:
        pytest.skip("MOCK=1")
    from vectordb.embed import Embedder
    from vectordb.seed import seeded_count
    reachability.mark_up(wv.weaviate_url())
    try:
        c = wv.get_client()
        n = seeded_count(c)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Weaviate not reachable: {e}")
    if n < len(load_events()):
        pytest.skip(f"Weaviate holds {n} events, corpus has {len(load_events())}: run scripts/seed_weaviate.py")
    emb = Embedder()
    if emb.is_fallback:
        pytest.skip("bge-small not available")
    yield emb
    wv.close_client()


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.mark.parametrize("exclude_holdout", [False, True])
@pytest.mark.parametrize("situation, event_type, region", QUERIES)
def test_weaviate_matches_numpy(live, situation, event_type, region, exclude_holdout):
    req = AnalogReq(situation=situation, event_type=event_type, region_hint=region, k=5, exclude_holdout=exclude_holdout)
    by_numpy = [e["event_id"] for _, e in numpy_search(req, live)[0]]
    by_weaviate = [e["event_id"] for _, e in weaviate_search(req, live)[0]]
    assert by_weaviate == by_numpy


def test_weaviate_is_the_primary_path(live):
    value, warnings, meta = asyncio.run(find_analogs(AnalogReq(situation="cyclone hits Odisha coast", event_type="cyclone"), live))
    assert meta.backend == "weaviate" and not meta.degraded, warnings
    assert value["analogs"]
