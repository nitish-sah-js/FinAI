import math
from datetime import datetime, timezone
from typing import Any


def _age_hours(published_at: datetime) -> float:
    now = datetime.now(timezone.utc)
    pub = published_at if published_at.tzinfo else \
          published_at.replace(tzinfo=timezone.utc)
    return max(0.0, (now - pub).total_seconds() / 3600)


def _get(h: Any, key: str, default=None):
    return h.get(key, default) if isinstance(h, dict) else getattr(h, key, default)


def _holdings(portfolio: Any | None) -> list:
    if portfolio is None:
        return []
    return portfolio.get("holdings", []) if isinstance(portfolio, dict) else list(getattr(portfolio, "holdings", []))


def missing_prices(portfolio: Any | None) -> list[str]:
    """Tickers whose avg_price is missing (the shared Portfolio model allows None)."""
    return [_get(h, "ticker") for h in _holdings(portfolio) if not _get(h, "avg_price")]


def _position_weights(portfolio: Any | None) -> dict[str, float]:
    """Returns {ticker: weight} normalised to sum=1. value = qty × avg_price; a missing price counts as 1
    (qty-weighted) instead of crashing on float(None)."""
    raw: dict[str, float] = {}
    for h in _holdings(portfolio):
        qty = float(_get(h, "qty") or 0)
        price = _get(h, "avg_price")
        raw[_get(h, "ticker")] = qty * float(price if price else 1.0)
    total = sum(raw.values()) or 1.0
    return {t: v / total for t, v in raw.items()}


def compute_weights(
        items: list[dict],
        portfolio: Any | None,
        warnings: list[str]) -> list[dict]:
    """
    Adds 'weight' to each item dict in-place.
    weight = position_weight × relevance × recency
    """
    pos_w = _position_weights(portfolio)
    missing = missing_prices(portfolio)
    if missing:
        warnings.append(f"avg_price missing for {', '.join(missing[:5])} — those holdings are weighted by quantity")

    weighted = []
    for item in items:
        ticker    = (item.get("tickers") or ["__unknown__"])[0]
        relevance = float(item.get("relevance", 0.5))
        age_h     = _age_hours(item["published_at"])
        recency   = math.exp(-age_h / 24)
        pos       = pos_w.get(ticker, 1.0 / max(len(items), 1))
        item["weight"] = round(pos * relevance * recency, 4)
        weighted.append(item)
    return weighted


def portfolio_sentiment(
        items: list[dict],
        portfolio: Any | None,
        warnings: list[str]) -> tuple[float, dict[str, float]]:
    """
    Returns (portfolio_sentiment_score, by_ticker_dict)
    """
    pos_w = _position_weights(portfolio)
    by_ticker: dict[str, list[tuple[float, float]]] = {}

    for item in items:
        score  = float(item.get("score", 0))
        weight = float(item.get("weight", 1))
        for ticker, _ in (item.get("ticker_matches") or [("__macro__", 0.2)]):
            by_ticker.setdefault(ticker, []).append((weight, score))

    agg: dict[str, float] = {}
    for ticker, pairs in by_ticker.items():
        total_w = sum(w for w, _ in pairs) or 1e-9
        agg[ticker] = round(
            sum(w * s for w, s in pairs) / total_w, 4)

    if not agg:
        return 0.0, {}

    if pos_w:
        covered = {t: v for t, v in pos_w.items() if t in agg}
        if covered:
            total = sum(covered.values()) or 1.0
            port_score = sum(
                (w / total) * agg[t] for t, w in covered.items())
        else:
            warnings.append("no portfolio tickers found in headlines")
            port_score = sum(agg.values()) / len(agg)
    else:
        port_score = sum(agg.values()) / len(agg)

    return round(port_score, 4), agg