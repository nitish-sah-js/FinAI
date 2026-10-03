"""The eval harness's own scorers must be right, or the prompt numbers mean nothing."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("run_evals", ROOT / "evals" / "run_evals.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)


def test_score_intent_field_accuracy():
    expect = {"intent": "event_impact", "event_type": "monsoon", "horizon_days": 20, "tools_include": ["agri"],
              "region_contains": "india", "tickers_include": ["ITC.NS"]}
    good = {"intent": "event_impact", "event_type": "monsoon", "horizon_days": 20, "needs_tools": ["agri", "weather"],
            "region": "Central India", "tickers": ["ITC.NS", "HINDUNILVR.NS"]}
    assert ev.score_intent(good, expect) == (1.0, [])
    bad = {**good, "horizon_days": 30, "needs_tools": ["weather"]}
    score, failed = ev.score_intent(bad, expect)
    assert score == 4 / 6 and set(failed) == {"horizon_days", "tools_include"}
    assert ev.score_intent(None, expect) == (0.0, ["invalid"])


def test_hedge_unchanged():
    evidence = [{"tool": "hedge", "value": {"proposals": [{"instrument": "NIFTY OCT FUT short", "quantity": 1}]}}]
    ok = "### Suggested hedges\n- Sell 1 lot NIFTY OCT FUT short (hedge ratio 0.42) [ev_hedge_001].\n### Confidence\nx"
    resized = ok.replace("1 lot", "3 lots")
    renamed = ok.replace("NIFTY OCT FUT short", "NIFTY futures")
    assert ev.hedge_unchanged(ok, evidence, "en")
    assert not ev.hedge_unchanged(resized, evidence, "en")
    assert not ev.hedge_unchanged(renamed, evidence, "en")
    hi = "### Hedge sujhav\n- Sell 1 lot NIFTY OCT FUT short [ev_hedge_001]."
    assert ev.hedge_unchanged(hi, evidence, "hinglish")


def test_sentence_count_ignores_citations():
    assert ev.n_sentences("Rain is +240% [ev_weather_001]. Coal is exposed. Confidence 0.62 [ev_weather_001].") == 3
    assert ev.n_sentences("One sentence with 0.42 decimals [ev_hedge_001].") == 1


def test_regression_detection(tmp_path):
    prev = {"summary": [{"prompt": "P4", "model": "m", "valid": 1.0, "grounded": 0.9}]}
    (tmp_path / "20260101_000000.json").write_text(json.dumps(prev))
    cur_file = tmp_path / "20260102_000000.json"
    same = [{"prompt": "P4", "model": "m", "valid": 1.0, "grounded": 0.9}]
    worse = [{"prompt": "P4", "model": "m", "valid": 1.0, "grounded": 0.7}]
    other_model = [{"prompt": "P4", "model": "other", "valid": 0.1, "grounded": 0.1}]
    assert ev.regressions(same, tmp_path, cur_file) == []
    assert ev.regressions(worse, tmp_path, cur_file) == ["P4 m grounded: 0.70 < best 0.90"]
    assert ev.regressions(other_model, tmp_path, cur_file) == []     # compared per (prompt, model) only


def test_mock_run_end_to_end():
    env = {**os.environ, "MOCK": "1", "MOCK_DELAY_MS": "0", "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, str(ROOT / "evals" / "run_evals.py"), "--no-save", "--cases", "2,3"],
                       capture_output=True, text=True, timeout=120, env=env)
    assert p.returncode == 0, p.stderr
    assert "PROMPT" in p.stdout and "P1-rules" in p.stdout
    # the canned cyclone answer is grounded on the cyclone bundle (case 2) but not on the monsoon bundle (case 3)
    assert "P4 case 3" in p.stdout and "P4 case 2" not in p.stdout
