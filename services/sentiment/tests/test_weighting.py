"""Tests for sentiment.weighting — weight computation and portfolio aggregation."""

import math
import pytest
from datetime import datetime, timezone, timedelta
from sentiment.weighting import (
    _age_hours,
    _position_weights,
    compute_weights,
    portfolio_sentiment,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_item(
    ticker="RELIANCE.NS", score=0.5, confidence=0.8,
    hours_ago=0, relevance=0.9,
):
    """Create a minimal scored-item dict for testing."""
    pub = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {
        "news_id":        f"n_{ticker}",
        "label":          "positive" if score > 0 else "negative",
        "score":          score,
        "confidence":     confidence,
        "published_at":   pub,
        "tickers":        [ticker],
        "ticker_matches": [(ticker, relevance)],
        "relevance":      relevance,
    }


def _make_portfolio(holdings_data):
    """Return a simple namespace object mimicking Portfolio model."""
    class _H:
        def __init__(self, t, q, p):
            self.ticker, self.qty, self.avg_price = t, q, p
    class _P:
        def __init__(self, hs):
            self.holdings = [_H(*h) for h in hs]
    return _P(holdings_data)


# ── _age_hours ────────────────────────────────────────────────────────────────

class TestAgeHours:
    def test_recent(self):
        pub = datetime.now(timezone.utc) - timedelta(hours=2)
        assert abs(_age_hours(pub) - 2.0) < 0.05

    def test_naive_datetime_treated_as_utc(self):
        pub = datetime.utcnow() - timedelta(hours=5)
        assert abs(_age_hours(pub) - 5.0) < 0.05

    def test_future_clamped_to_zero(self):
        pub = datetime.now(timezone.utc) + timedelta(hours=1)
        assert _age_hours(pub) == 0.0


# ── _position_weights ────────────────────────────────────────────────────────

class TestPositionWeights:
    def test_none_portfolio(self):
        assert _position_weights(None) == {}

    def test_single_holding(self):
        p = _make_portfolio([("RELIANCE.NS", 10, 2500)])
        w = _position_weights(p)
        assert w == {"RELIANCE.NS": 1.0}

    def test_two_holdings_proportional(self):
        p = _make_portfolio([
            ("RELIANCE.NS", 10, 100),  # value = 1000
            ("TCS.NS",       5, 200),  # value = 1000
        ])
        w = _position_weights(p)
        assert abs(w["RELIANCE.NS"] - 0.5) < 1e-9
        assert abs(w["TCS.NS"]      - 0.5) < 1e-9


# ── compute_weights ──────────────────────────────────────────────────────────

class TestComputeWeights:
    def test_fresh_item_no_portfolio(self):
        items = [_make_item(hours_ago=0, relevance=1.0)]
        warnings = []
        compute_weights(items, None, warnings)
        # pos = 1/1 = 1.0, relevance = 1.0, recency ≈ 1.0
        assert items[0]["weight"] > 0.9

    def test_old_item_decays(self):
        fresh = _make_item(hours_ago=0, relevance=1.0)
        stale = _make_item(hours_ago=48, relevance=1.0)
        warnings = []
        compute_weights([fresh, stale], None, warnings)
        assert fresh["weight"] > stale["weight"]

    def test_relevance_affects_weight(self):
        hi = _make_item(relevance=1.0, hours_ago=0)
        lo = _make_item(relevance=0.2, hours_ago=0)
        warnings = []
        compute_weights([hi, lo], None, warnings)
        assert hi["weight"] > lo["weight"]

    def test_portfolio_position_affects_weight(self):
        items = [
            _make_item(ticker="RELIANCE.NS", hours_ago=0, relevance=1.0),
            _make_item(ticker="TCS.NS",      hours_ago=0, relevance=1.0),
        ]
        # RELIANCE has 4× the position value
        p = _make_portfolio([
            ("RELIANCE.NS", 10, 400),  # value = 4000
            ("TCS.NS",       5, 200),  # value = 1000
        ])
        warnings = []
        compute_weights(items, p, warnings)
        assert items[0]["weight"] > items[1]["weight"]


# ── portfolio_sentiment ──────────────────────────────────────────────────────

class TestPortfolioSentiment:
    def test_empty_items(self):
        score, by_ticker = portfolio_sentiment([], None, [])
        assert score == 0.0
        assert by_ticker == {}

    def test_single_positive(self):
        items = [_make_item(score=0.8, hours_ago=0)]
        compute_weights(items, None, [])
        score, by_ticker = portfolio_sentiment(items, None, [])
        assert score > 0
        assert "RELIANCE.NS" in by_ticker

    def test_portfolio_weighted_aggregation(self):
        items = [
            _make_item(ticker="RELIANCE.NS", score=0.9, hours_ago=0),
            _make_item(ticker="TCS.NS",      score=-0.5, hours_ago=0),
        ]
        p = _make_portfolio([
            ("RELIANCE.NS", 10, 100),
            ("TCS.NS",       5, 100),
        ])
        compute_weights(items, p, [])
        score, by_ticker = portfolio_sentiment(items, p, [])
        # RELIANCE has 2× weight and positive → overall should be positive
        assert score > 0

    def test_warning_when_no_portfolio_tickers_in_headlines(self):
        items = [_make_item(ticker="INFY.NS", score=0.5, hours_ago=0)]
        p = _make_portfolio([("RELIANCE.NS", 10, 100)])
        warnings = []
        compute_weights(items, p, warnings)
        portfolio_sentiment(items, p, warnings)
        assert any("no portfolio tickers" in w for w in warnings)
