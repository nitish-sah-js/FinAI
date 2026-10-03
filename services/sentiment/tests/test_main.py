"""Tests for the /sentiment/score and /health endpoints via TestClient."""

import os
import re
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, timezone

# Prevent real model loading during import and TestClient startup
os.environ.pop("MOCK", None)

from fastapi.testclient import TestClient
from sentiment.main import app


# ── Fixtures ──────────────────────────────────────────────────────────────────

SAMPLE_REQUEST = {
    "items": [
        {
            "news_id":      "n1",
            "title":        "Reliance posts record quarterly profit",
            "summary":      "Strong refining margins boosted earnings",
            "source":       "ET",
            "published_at": "2026-10-03T10:00:00Z",
            "tickers":      ["RELIANCE.NS"],
        },
        {
            "news_id":      "n2",
            "title":        "TCS warns of weak demand in Europe",
            "summary":      "Management cuts guidance for next quarter",
            "source":       "Mint",
            "published_at": "2026-10-03T09:00:00Z",
            "tickers":      ["TCS.NS"],
        },
    ],
    "second_opinion": False,
    "portfolio": {
        "holdings": [
            {"ticker": "RELIANCE.NS", "qty": 10, "avg_price": 2500},
            {"ticker": "TCS.NS",      "qty":  5, "avg_price": 3500},
        ]
    },
}


def _fake_finbert_results(texts):
    """Return plausible FinBERT-like dicts for each text."""
    results = []
    for t in texts:
        if "profit" in t.lower() or "strong" in t.lower():
            results.append({
                "label": "positive", "score": 0.75,
                "confidence": 0.88,
                "probs": {"positive": 0.88, "negative": 0.06, "neutral": 0.06},
            })
        else:
            results.append({
                "label": "negative", "score": -0.60,
                "confidence": 0.82,
                "probs": {"positive": 0.09, "negative": 0.82, "neutral": 0.09},
            })
    return results


def _noop_startup():
    """Patches to skip FinBERT and ticker loading during tests."""
    return [
        patch("sentiment.main.startup", new_callable=AsyncMock),
        patch("sentiment.main.score_async", new_callable=AsyncMock,
              side_effect=lambda texts: _fake_finbert_results(texts)),
        patch("sentiment.main.search_tickers", new_callable=AsyncMock,
              side_effect=lambda text, explicit: [
                  (t, 1.0) for t in explicit
              ] if explicit else [("UNKNOWN.NS", 0.3)]),
        patch("sentiment.model.get_scorer", return_value=MagicMock()),
    ]


@pytest.fixture
def client():
    """FastAPI TestClient with FinBERT + ticker_map + startup mocked out."""
    patches = _noop_startup()
    for p in patches:
        p.start()
    try:
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c
    finally:
        for p in patches:
            p.stop()


# ── /health ───────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health(self):
        patches = _noop_startup()
        for p in patches:
            p.start()
        try:
            with TestClient(app) as c:
                r = c.get("/health")
                assert r.status_code == 200
                body = r.json()
                # shared Health model (01 §5): "degraded" is legitimate here (e.g. gemma3 not pulled on this laptop)
                assert body["status"] in ("ok", "degraded")
                assert body["service"] == "sentiment"
                assert "finbert" in body["deps"] and "second_opinion" in body["deps"]
        finally:
            for p in patches:
                p.stop()


# ── /sentiment/score ──────────────────────────────────────────────────────────

class TestScoreEndpoint:
    def test_basic_response_structure(self, client):
        r = client.post("/sentiment/score", json=SAMPLE_REQUEST)
        assert r.status_code == 200
        body = r.json()

        assert "evidence" in body
        assert "warnings" in body
        assert len(body["evidence"]) == 1

        ev = body["evidence"][0]
        assert ev["tool"] == "sentiment"
        assert "value" in ev
        assert "items" in ev["value"]
        assert "portfolio_sentiment" in ev["value"]
        assert "by_ticker" in ev["value"]
        assert isinstance(ev["confidence"], float)

    def test_items_match_input_count(self, client):
        r = client.post("/sentiment/score", json=SAMPLE_REQUEST)
        items = r.json()["evidence"][0]["value"]["items"]
        assert len(items) == 2

    def test_item_has_required_fields(self, client):
        r = client.post("/sentiment/score", json=SAMPLE_REQUEST)
        item = r.json()["evidence"][0]["value"]["items"][0]
        required = {"news_id", "label", "score", "confidence",
                    "model", "weight", "relevance", "hinglish", "tickers"}
        assert required.issubset(item.keys())

    def test_dedup_removes_duplicate_titles(self, client):
        req = {
            "items": [
                {"news_id": "n1", "title": "Same headline",
                 "published_at": "2026-10-03T10:00:00Z"},
                {"news_id": "n2", "title": "Same headline",
                 "published_at": "2026-10-03T10:00:00Z"},
            ],
            "second_opinion": False,
        }
        r = client.post("/sentiment/score", json=req)
        items = r.json()["evidence"][0]["value"]["items"]
        assert len(items) == 1

    def test_empty_items_returns_warning(self, client):
        req = {"items": [], "second_opinion": False}
        r = client.post("/sentiment/score", json=req)
        body = r.json()
        assert body["evidence"] == []
        assert any("no items" in w for w in body["warnings"])

    def test_few_headlines_warning(self, client):
        req = {
            "items": [
                {"news_id": "n1", "title": "Only one headline",
                 "published_at": "2026-10-03T10:00:00Z"},
            ],
            "second_opinion": False,
        }
        r = client.post("/sentiment/score", json=req)
        body = r.json()
        assert any("few headlines" in w for w in body["warnings"])
        ev = body["evidence"][0]
        assert ev["confidence"] <= 0.3

    def test_custom_run_id(self, client):
        req = {**SAMPLE_REQUEST, "run_id": "my_custom_run"}
        r = client.post("/sentiment/score", json=req)
        ev = r.json()["evidence"][0]
        assert ev["run_id"] == "my_custom_run"
        # contract id format ev_<tool>_<nnn> (01 §4); the orchestrator renumbers per run anyway
        assert re.fullmatch(r"ev_sentiment_\d{3}", ev["id"])

    def test_mock_mode(self, monkeypatch):
        from copilot_common.settings import reload_settings
        monkeypatch.setenv("MOCK", "1")
        monkeypatch.setenv("MOCK_DELAY_MS", "0")
        reload_settings()                      # settings are cached; MOCK is read through them
        patches = _noop_startup()
        for p in patches:
            p.start()
        try:
            with TestClient(app) as c:
                r = c.post("/sentiment/score", json=SAMPLE_REQUEST)
                body = r.json()
                assert body["evidence"][0]["degraded"] is True
                assert body["evidence"][0]["degraded_reason"] == "mock"
        finally:
            monkeypatch.delenv("MOCK", raising=False)
            reload_settings()
            for p in patches:
                p.stop()

    def test_portfolio_sentiment_in_response(self, client):
        r = client.post("/sentiment/score", json=SAMPLE_REQUEST)
        ev = r.json()["evidence"][0]
        ps = ev["value"]["portfolio_sentiment"]
        assert isinstance(ps, float)

    def test_no_internal_fields_leak(self, client):
        """Ensure _warning and other internal keys don't leak into response items."""
        r = client.post("/sentiment/score", json=SAMPLE_REQUEST)
        items = r.json()["evidence"][0]["value"]["items"]
        for item in items:
            assert "_warning" not in item
            assert "ticker_matches" not in item
            assert "published_at" not in item
