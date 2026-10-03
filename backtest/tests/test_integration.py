"""Backtest integration: events.json, contract fields, leakage guards, the shared price helper."""
import asyncio
from pathlib import Path

import pytest

from backtest.events import load_cases
from backtest.extract import extract_prediction, holdout_analogs, parse_range

ROOT = Path(__file__).resolve().parents[2]
EVENTS = ROOT / "data" / "events.json"


def test_events_json_has_the_8_holdout_cases_of_doc_12():
    cases = load_cases(EVENTS, "holdout")
    assert [c.event_id for c in cases] == [
        "hurricane_ida_2021", "cyclone_biparjoy_2023", "cyclone_michaung_2023", "monsoon_deficit_2015",
        "heatwave_2022", "rbi_offcycle_hike_2022", "abqaiq_attack_2019", "rice_export_ban_2023"]
    assert all(c.as_of <= c.t0 for c in cases) and all(len(c.assets) == 2 for c in cases)
    assert next(c for c in cases if c.event_id == "monsoon_deficit_2015").horizon_days == 20


def test_orchestrator_holdings_impact_string_ranges_are_parsed():
    # the orchestrator writes range as a STRING; the original extract only handled [lo, hi] lists
    final = {"confidence": "medium", "evidence": [],
             "holdings_impact": [{"ticker": "COALINDIA.NS", "impact": "negative", "range": "-4.1% to +1.2% (5d)"}]}
    p = extract_prediction(final, "COALINDIA.NS")
    assert p["p10"] == pytest.approx(-0.041) and p["p90"] == pytest.approx(0.012)
    assert p["median"] == pytest.approx(-0.0145) and p["direction"] == -1 and p["source"] == "holdings_impact.range"
    assert parse_range("n/a") is None and parse_range([-0.02, 0.03]) == (-0.02, 0.03)


def test_holdout_analog_detection():
    final = {"evidence": [{"tool": "analogs", "value": {"analogs": [{"event_id": "cyclone_fani_2019"},
                                                                    {"event_id": "cyclone_biparjoy_2023"}]}}]}
    assert holdout_analogs(final, {"cyclone_biparjoy_2023", "hurricane_ida_2021"}) == ["cyclone_biparjoy_2023"]


def test_query_request_carries_exclude_holdout():
    from copilot_common.models import QueryRequest
    assert QueryRequest(query="x", exclude_holdout=True).exclude_holdout is True
    assert QueryRequest(query="x").exclude_holdout is False


def test_client_sends_as_of_and_exclude_holdout(monkeypatch):
    import httpx
    from backtest import client as C
    from backtest.events import BacktestCase
    sent = {}

    def handler(req: httpx.Request):
        if req.method == "POST":
            sent.update(__import__("json").loads(req.content))
            return httpx.Response(200, json={"run_id": "run_1", "ws_url": "ws://x"})
        return httpx.Response(200, json={"status": "done", "final": {"run_id": "run_1"}, "events": []})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    case = BacktestCase("e", "2021-08-29", "2021-08-29", ["NG=F"], "q")
    run_id, final, _ = asyncio.run(C.run_case(case, base_url="http://orch"))
    assert run_id == "run_1" and sent["as_of"] == "2021-08-29" and sent["exclude_holdout"] is True


def test_prices_fall_back_to_yfinance_then_replay_offline(tmp_path, monkeypatch):
    """ingestion down → yfinance; the cached window then replays with CACHE_MODE=replay and no network at all."""
    from copilot_common import prices
    from copilot_common.settings import reload_settings
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CACHE_MODE", "record")
    reload_settings()
    calls = {"yf": 0}

    async def ingest_down(*a, **k):
        raise prices.PriceUnavailable("down")

    def yf(symbol, start, end):
        calls["yf"] += 1
        return [("2021-08-26", 100.0), ("2021-08-27", 110.0), ("2021-08-30", 111.0)]
    monkeypatch.setattr(prices, "_from_ingestion", ingest_down)
    monkeypatch.setattr(prices, "_from_yfinance", yf)
    rows, src = prices.get_closes_sync("NG=F", "2021-08-20", "2021-09-10")
    assert src.startswith("yfinance") and len(rows) == 3
    rows2, _ = prices.get_closes_sync("NG=F", "2021-08-20", "2021-09-10", as_of="2021-08-27")
    assert rows2 == [("2021-08-26", 100.0), ("2021-08-27", 110.0)] and calls["yf"] == 1   # cache hit, as_of applied
    monkeypatch.setenv("CACHE_MODE", "replay")
    reload_settings()
    monkeypatch.setattr(prices, "_from_yfinance", lambda *a: (_ for _ in ()).throw(AssertionError("network in replay")))
    assert prices.get_closes_sync("NG=F", "2021-08-20", "2021-09-10")[0] == rows
    with pytest.raises(prices.PriceUnavailable):           # uncached window in replay → clear error, no network
        prices.get_closes_sync("CL=F", "2021-08-20", "2021-09-10")
    reload_settings()


def test_scoreboard_n_counts_points_not_only_directional_calls():
    from backtest.run_backtest import build_scoreboard
    base = {"median": 0.0, "p10": -0.02, "p90": 0.02, "direction": 0}
    pts = [{"event_id": "e", "asset": "A", "as_of": "2021-08-29", "run_id": "r", "realized": r, "copilot": None,
            "price_only": {**base, "direction": 1, "median": 0.01}, "sentiment_only": base, "zero": base,
            "top_similarity": None} for r in (0.02, -0.01, 0.03)]
    sb = build_scoreboard(pts, {"llm_mode": "local", "horizon_days": 5, "split": "holdout", "n_events": 1, "n_points": 3})
    assert sb["methods"]["zero"]["n"] == 3 and sb["methods"]["sentiment_only"]["n"] == 3
    assert sb["methods"]["sentiment_only"]["n_directional"] == 0 and sb["methods"]["price_only"]["n_directional"] == 3
