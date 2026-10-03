import numpy as np
from backtest import baselines, metrics
from backtest.calibration import calibration_bins
from backtest.extract import check_leakage, extract_prediction
from backtest.realized import realized_from_closes
from backtest.run_backtest import build_scoreboard

FINAL = {"confidence": "medium", "evidence": [
    {"id": "ev_analogs_001", "tool": "analogs", "as_of": "2021-08-27T00:00:00Z", "value": {"distribution": [
        {"asset": "NG=F", "horizon": "5d", "median": 0.048, "p10": -0.012, "p90": 0.10},
        {"asset": "CL=F", "horizon": "5d", "median": 0.001, "p10": -0.03, "p90": 0.03}]}}],
    "holdings_impact": [{"ticker": "BPCL.NS", "impact": "negative", "range": [-0.04, 0.02], "evidence_ids": []}]}


def test_extract_distribution():
    p = extract_prediction(FINAL, "NG=F", "5d")
    assert p["median"] == 0.048 and p["direction"] == 1 and p["confidence"] == 0.65
    assert extract_prediction(FINAL, "CL=F")["direction"] == 0  # |0.001| < 0.002


def test_extract_fallback_and_missing():
    p = extract_prediction(FINAL, "BPCL.NS")
    assert abs(p["median"] + 0.01) < 1e-12 and p["direction"] == -1
    assert extract_prediction(FINAL, "XYZ") is None


def test_metrics_handmade():
    assert metrics.hit_rate([1, -1, 0, 1], [0.1, 0.1, 0.5, 0.2]) == (2 / 3, 3)
    assert abs(metrics.mae([0.1, 0.0], [0.0, 0.1]) - 0.1) < 1e-12
    assert metrics.coverage_80([-1, -1], [1, 0], [0.5, 0.5]) == 0.5
    assert abs(metrics.brier([0.8, 0.6], [1, 1], [1, -1]) - ((0.2 ** 2 + 0.6 ** 2) / 2)) < 1e-12
    assert metrics.skill_vs_zero(0.03, 0.04) == 0.25
    ci = metrics.bootstrap_ci([1, 0, 1, 1, 0, 1])
    assert ci == metrics.bootstrap_ci([1, 0, 1, 1, 0, 1]) and ci[0] <= ci[1]


def test_calibration_bins():
    b = calibration_bins([0.65, 0.65, 0.55], [1, 1, -1], [0.1, -0.1, -0.1])
    assert b[1] == {"stated": 0.65, "observed": 0.5, "n": 2} and b[0]["observed"] == 1.0 and b[2]["n"] == 0


def _closes(n=300):
    from datetime import date, timedelta
    d0 = date(2021, 1, 1)
    return [((d0 + timedelta(days=i)).isoformat(), 100 + i * 0.1 + (i % 7)) for i in range(n)]


def test_baselines_ignore_future():
    c = _closes()
    as_of = c[200][0]
    a = baselines.price_only(c, as_of)
    c2 = c[:201] + [(d, v * 5) for d, v in c[201:]]  # corrupt everything after as_of
    assert a == baselines.price_only(c2, as_of)
    assert baselines.zero(c, as_of)["median"] == 0
    s = baselines.sentiment_only(c, as_of, -0.3)
    assert s["direction"] == -1 and s["median"] < 0


def test_realized_uses_base_on_or_before_as_of():
    closes = [("2021-08-26", 100), ("2021-08-27", 110), ("2021-08-30", 111), ("2021-08-31", 112),
              ("2021-09-01", 113), ("2021-09-02", 114), ("2021-09-03", 121)]
    assert abs(realized_from_closes(closes, "2021-08-29", 5) - (121 / 110 - 1)) < 1e-12  # Sunday as_of
    assert realized_from_closes(closes, "2021-08-29", 9) is None


def test_leakage():
    assert check_leakage(FINAL, [], "2021-08-29") == []
    bad = {"evidence": [{"id": "ev_news_001", "tool": "news", "as_of": "2021-09-02T10:00:00Z"}]}
    assert check_leakage(bad, [], "2021-08-29")


def test_scoreboard_shape():
    def pt(real, d):
        base = {"median": 0.01 * d, "p10": -0.02, "p90": 0.03, "direction": d}
        return {"event_id": "e", "asset": "A", "as_of": "2021-08-29", "run_id": "r", "realized": real,
                "copilot": {**base, "confidence": 0.65}, "price_only": base, "sentiment_only": base,
                "zero": {"median": 0, "p10": -0.02, "p90": 0.02, "direction": 0}, "top_similarity": 0.61}
    sb = build_scoreboard([pt(0.02, 1), pt(-0.02, 1)], {"llm_mode": "local", "horizon_days": 5, "split": "holdout",
                                                          "n_events": 1, "n_points": 2})
    assert set(sb) == {"generated_at", "config", "methods", "rows", "calibration", "misses", "disclaimer"}
    assert sb["methods"]["copilot"]["hit_rate"] == 0.5 and len(sb["misses"]) == 1
    assert sb["methods"]["zero"]["hit_rate"] is None


def test_parse_bars_toolresult():
    from backtest.realized import parse_bars
    tr = {"evidence": [{"value": {"ticker": "X", "interval": "1d", "rows": [
        {"date": "2021-08-27", "close": 110.0}, {"date": "2021-08-26", "close": 100.0}]}}]}
    assert parse_bars(tr) == [("2021-08-26", 100.0), ("2021-08-27", 110.0)]
