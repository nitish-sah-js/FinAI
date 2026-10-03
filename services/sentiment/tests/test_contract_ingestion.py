"""Contract test: the payload the ingestion worker really sends must be accepted by /sentiment/score.

Regression for the 2026-10-04 bug where the worker omitted published_at and every call returned 422.
"""
import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from copilot_common.models import NewsScoreItem, SentimentScoreRequest
from sentiment.main import app

from .test_main import _noop_startup

ROOT = Path(__file__).resolve().parents[3]


def _ingestion_worker():
    """Import services/ingestion/worker.py as a package module (it uses relative imports)."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if importlib.util.find_spec("services.ingestion.worker") is None:
        pytest.skip("ingestion service not present")
    return importlib.import_module("services.ingestion.worker")


# items exactly as the ingestion RSS/GDELT sources produce them (sources/rss.py)
FEED_ITEMS = [
    {"news_id": "a1", "title": "Reliance posts record quarterly profit", "summary": "Strong margins", "source": "ET",
     "url": "https://example.com/a1", "published_at": "2026-10-03T10:00:00+00:00", "tickers": ["RELIANCE.NS"]},
    {"news_id": "a2", "title": "TCS warns of weak demand", "summary": "", "source": "Mint",
     "url": "https://example.com/a2", "published_at": None, "tickers": ["TCS.NS"]},          # no feed date
    {"news_id": "a3", "title": "ITC volumes steady", "summary": "", "source": "BS",
     "url": "https://example.com/a3", "published_at": "not a date", "tickers": []},          # unparseable date
]


def test_worker_payload_matches_shared_contract():
    payload = _ingestion_worker().sentiment_payload(FEED_ITEMS)
    req = SentimentScoreRequest.model_validate(payload)
    assert [i.news_id for i in req.items] == ["a1", "a2", "a3"]
    assert all(i.published_at is not None for i in req.items)          # missing / bad dates fall back to now
    assert req.items[0].published_at.isoformat().startswith("2026-10-03T10:00")


def test_missing_published_at_defaults_to_now():
    item = NewsScoreItem(news_id="x", title="t")
    assert item.published_at.tzinfo is not None


def test_sentiment_accepts_real_ingestion_payload():
    payload = _ingestion_worker().sentiment_payload(FEED_ITEMS)
    payload["second_opinion"] = False                                     # keep the test offline
    patches = _noop_startup()
    for p in patches:
        p.start()
    try:
        with TestClient(app) as c:
            r = c.post("/sentiment/score", json=payload)
            assert r.status_code == 200, r.text
            items = r.json()["evidence"][0]["value"]["items"]
            assert {i["news_id"] for i in items} == {"a1", "a2", "a3"}
            # and a raw item with no published_at at all is also accepted
            r2 = c.post("/sentiment/score", json={"items": [{"news_id": "z", "title": "Crude jumps"}], "second_opinion": False})
            assert r2.status_code == 200, r2.text
    finally:
        for p in patches:
            p.stop()
