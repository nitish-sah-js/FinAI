"""Each detector: trigger and no-trigger cases (11 §3), scoring table (§4), weather alert normalisation."""
import pytest

from monitor.detectors import (Candidate, detect_agri_stress, detect_news_burst, detect_price_z,
                               detect_sentiment_shift, detect_volume_z, detect_weather_threshold, weather_kinds)
from monitor.scoring import apply_watchlist, impact_score, tier
import math

TH = {"price_z": 3.0, "volume_z": 3.0, "news_burst_z": 3.0, "news_burst_k": 3, "news_lambda_floor": 0.5,
      "sentiment_shift": 0.4, "sentiment_min_n": 3}
WSEV = {"cyclone": 0.9, "hurricane": 0.9, "heavy_rain": 0.6, "heatwave": 0.5, "rain_deficit": 0.5}
LINKS = {"OD-Puri": [{"ticker": "ADANIPORTS.NS", "strength": 0.8}, {"ticker": "ONGC.NS", "strength": 0.5}]}


def test_price_z_trigger_boundary_and_no_trigger():
    c = detect_price_z([{"ticker": "ONGC.NS", "r_5m": 0.009, "sigma_5m": 0.003, "history_days": 20}], TH)
    assert len(c) == 1 and c[0].facts["z"] == 3.0 and c[0].confidence == 0.8 and c[0].severity == 0.5  # float boundary
    neg = detect_price_z([{"ticker": "ONGC.NS", "r_5m": -0.024, "sigma_5m": 0.004, "history_days": 5}], TH)
    assert neg[0].severity == 1.0 and neg[0].confidence == 0.5
    assert detect_price_z([{"ticker": "X", "r_5m": 0.005, "sigma_5m": 0.003}], TH) == []
    assert detect_price_z([{"ticker": "X", "r_5m": 0.005, "sigma_5m": 0}, {"ticker": "Y", "r_5m": 0.1, "sigma_5m": None}], TH) == []


def test_volume_z():
    mu, sd = math.log(1e5), 0.3
    hit = detect_volume_z([{"ticker": "ITC.NS", "v_30m": 1e5 * math.exp(3.6 * sd), "mean_log_v": mu, "std_log_v": sd}], TH)
    assert hit and abs(hit[0].facts["z"] - 3.6) < 1e-6 and hit[0].confidence == 0.7
    assert detect_volume_z([{"ticker": "ITC.NS", "v_30m": 1.2e5, "mean_log_v": mu, "std_log_v": sd}], TH) == []


def test_news_burst_doc_example_and_sector_relevance():
    c = detect_news_burst([{"ticker": "ITC.NS", "k": 5, "lambda_h": 0.9, "tagged": True}], TH)
    assert c and c[0].facts["z"] == pytest.approx(4.32, abs=0.01) and c[0].relevance == 1.0
    s = detect_news_burst([{"ticker": "ITC.NS", "k": 5, "lambda_h": 0.1, "tagged": False}], TH)
    assert s[0].relevance == 0.5 and s[0].facts["lambda"] == 0.5          # λ floor
    assert detect_news_burst([{"ticker": "ITC.NS", "k": 2, "lambda_h": 0.0}], TH) == []   # k < 3
    assert detect_news_burst([{"ticker": "ITC.NS", "k": 4, "lambda_h": 2.0}], TH) == []   # z = 1.4


def test_sentiment_shift():
    c = detect_sentiment_shift([{"ticker": "ITC.NS", "mean_6h": -0.5, "mean_24h": 0.1, "n_6h": 3, "conf": 0.7}], TH)
    assert c and c[0].facts["delta"] == -0.6 and c[0].confidence == 0.7
    assert detect_sentiment_shift([{"ticker": "ITC.NS", "mean_6h": -0.5, "mean_24h": 0.1, "n_6h": 2, "conf": 0.7}], TH) == []
    assert detect_sentiment_shift([{"ticker": "ITC.NS", "mean_6h": 0.2, "mean_24h": 0.0, "n_6h": 9, "conf": 0.7}], TH) == []


def test_weather_kinds_from_kinds_free_text_and_storm():
    assert weather_kinds({"alerts": ["cyclone", "heavy_rain"]}) == ["cyclone", "heavy_rain"]
    v = {"alerts": ["IMD red alert: Puri, Kendrapara"], "storm": {"name": "Dana", "basin": "Bay of Bengal"}}
    assert weather_kinds(v) == ["cyclone"]
    assert weather_kinds({"alerts": ["Heat wave warning"]}) == ["heatwave"]
    assert weather_kinds({"storm": {"basin": "Atlantic"}}) == ["hurricane"]
    assert weather_kinds({"alerts": []}) == []


def test_weather_one_candidate_per_region_with_link_strength():
    rows = [{"region": "OD-Puri", "kinds": ["cyclone", "heavy_rain"], "confidence": 0.75, "evidence_id": "ev_weather_001"}]
    c = detect_weather_threshold(rows, LINKS, WSEV)
    assert len(c) == 1 and c[0].tickers == ["ADANIPORTS.NS", "ONGC.NS"] and c[0].severity == 0.9
    assert c[0].relevance == 0.8 and c[0].cooldown_key() == "weather_threshold:OD-Puri"
    held_only = detect_weather_threshold(rows, LINKS, WSEV, held={"ONGC.NS"})
    assert held_only[0].tickers == ["ONGC.NS"] and held_only[0].relevance == 0.5
    assert detect_weather_threshold([{"region": "OD-Puri", "kinds": [], "confidence": None}], LINKS, WSEV) == []
    nc = detect_weather_threshold([{"region": "OD-Puri", "kinds": ["heatwave"], "confidence": None}], LINKS, WSEV)
    assert nc[0].confidence == 0.5                                    # Evidence.confidence may be None


def test_agri_fires_only_on_transition():
    sev = {"stressed": 0.6, "severe": 0.9}
    links = {"MH-Yavatmal": [{"ticker": "UPL.NS", "strength": 0.6}]}
    row = {"region": "MH-Yavatmal", "stress_class": "severe", "prev_class": "watch", "confidence": 0.5}
    c = detect_agri_stress([row], links, sev)
    assert c and c[0].severity == 0.9 and c[0].cooldown_key() == "agri_stress:MH-Yavatmal"
    assert detect_agri_stress([{**row, "prev_class": "stressed"}], links, sev) == []
    assert detect_agri_stress([{**row, "prev_class": None}], links, sev) == []        # first poll: record only
    assert detect_agri_stress([{**row, "stress_class": "watch"}], links, sev) == []


@pytest.mark.parametrize("impact, conf, expected", [(0.6, 0.6, 3), (0.9, 0.59, 2), (0.3, 0.9, 2), (0.29, 0.9, 1),
                                                    (0.66, 0.75, 3)])
def test_tier_table(impact, conf, expected):
    assert tier(impact, conf) == expected


def test_impact_score_exposure():
    c = Candidate(kind="price_z", tickers=["A"], severity=1.0, relevance=1.0, confidence=0.8, facts={})
    assert impact_score(c, {"A": 0.25}) == 1.0
    assert impact_score(c, {"A": 0.0}) == 0.4
    assert impact_score(c, {"A": 0.125}) == 0.7


def test_watchlist_relevance_and_unheld_dropped():
    c = Candidate(kind="price_z", tickers=["W"], severity=1.0, relevance=1.0, confidence=0.8, facts={})
    assert apply_watchlist(c, {"A"}, {"W"}, 0.3).relevance == 0.3
    assert apply_watchlist(c, {"A"}, set(), 0.3) is None
