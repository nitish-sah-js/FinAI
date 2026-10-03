"""build_events: outcome math on synthetic prices (no look-ahead), seed validation, corpus integrity."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_spec = importlib.util.spec_from_file_location("build_events", Path(__file__).resolve().parents[1] / "scripts" / "build_events.py")
be = importlib.util.module_from_spec(_spec)
sys.modules["build_events"] = be
_spec.loader.exec_module(be)

from vectordb.corpus import load_events      # noqa: E402
from vectordb.paths import seed_path         # noqa: E402

EVENT = "2022-06-15"


def synthetic(beta: float = 1.5, n_pre: int = 200, n_post: int = 30, seed: int = 0):
    """Business-day prices; asset returns = beta * bench returns + small noise; event jump of +10% on EVENT."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp(EVENT) - pd.Timedelta(days=1), periods=n_pre).append(
        pd.bdate_range(start=EVENT, periods=n_post))
    rb = rng.normal(0, 0.01, len(idx))
    ra = beta * rb + rng.normal(0, 0.001, len(idx))
    ev_pos = idx.get_loc(pd.Timestamp(EVENT))
    ra[ev_pos] += 0.10
    bench = pd.Series(100 * np.cumprod(1 + rb), index=idx)
    asset = pd.Series(50 * np.cumprod(1 + ra), index=idx)
    return asset, bench, ev_pos


def test_outcome_math_matches_definition():
    asset, bench, ev = synthetic()
    o, why = be.compute_outcome(asset, bench, EVENT, asset="X.NS", benchmark="^NSEI")
    assert why is None
    t0 = ev - 1
    assert o["t0_date"] == asset.index[t0].date().isoformat() and o["t0_date"] < EVENT
    for k in (1, 5, 20):
        r = asset.iloc[t0 + k] / asset.iloc[t0] - 1
        rb = bench.iloc[t0 + k] / bench.iloc[t0] - 1
        assert o[f"ret_{k}d"] == pytest.approx(r, abs=1e-6)
        assert o[f"abnormal_{k}d"] == pytest.approx(r - o["beta_used"] * rb, abs=2e-6)
        assert o["realized_dates"][f"{k}d"] == asset.index[t0 + k].date().isoformat()
    assert o["beta_used"] == pytest.approx(1.5, abs=0.05)
    assert o["ret_1d"] > 0.08                                   # the event-day jump is inside ret_1d
    assert o["est_window"][1] < o["t0_date"]


def test_no_lookahead_beta_uses_only_pre_event_window():
    asset, bench, ev = synthetic()
    o1, _ = be.compute_outcome(asset, bench, EVENT, asset="X.NS", benchmark="^NSEI")
    a2 = asset.copy()
    a2.iloc[ev - 9:] *= np.linspace(1, 3, len(a2) - (ev - 9))   # distort everything from t0-8 on
    o2, _ = be.compute_outcome(a2, bench, EVENT, asset="X.NS", benchmark="^NSEI")
    assert o2["beta_used"] == o1["beta_used"]                   # beta window ends at t0-10
    assert o2["ret_5d"] != o1["ret_5d"]
    a3 = asset.copy()
    a3.iloc[: ev - 1 - 131] *= 0.5                             # before the estimation window: no effect at all
    o3, _ = be.compute_outcome(a3, bench, EVENT, asset="X.NS", benchmark="^NSEI")
    assert {k: o3[k] for k in o3 if k != "est_window"} == {k: o1[k] for k in o1 if k != "est_window"}


def test_benchmark_itself_has_no_abnormal():
    _, bench, _ = synthetic()
    o, why = be.compute_outcome(bench, None, EVENT, asset="^NSEI", benchmark="^NSEI")
    assert why is None and o["beta_used"] == 1.0
    assert o["abnormal_1d"] is None and o["abnormal_5d"] is None and o["abnormal_20d"] is None


@pytest.mark.parametrize("n_pre,n_post,reason", [(100, 30, "trading days before t0"), (200, 10, "after event")])
def test_skips_with_reason_instead_of_inventing(n_pre, n_post, reason):
    asset, bench, _ = synthetic(n_pre=n_pre, n_post=n_post)
    o, why = be.compute_outcome(asset, bench, EVENT, asset="X.NS", benchmark="^NSEI")
    assert o is None and reason in why


def test_non_positive_price_is_skipped():
    asset, bench, ev = synthetic()
    asset.iloc[ev + 2] = -5.0
    o, why = be.compute_outcome(asset, bench, EVENT, asset="CL=F", benchmark="SPY")
    assert o is None and "non-positive" in why


def test_no_synthetic_outcome_generator_left():
    src = (Path(be.__file__)).read_text(encoding="utf-8")
    assert "generate_fallback_outcome" not in src and "hash(" not in src and "random" not in src.lower()


def test_seed_csv_is_well_formed():
    events = be.parse_seed(seed_path())
    assert len(events) == 40
    assert {e["split"] for e in events} == {"train", "holdout"}
    assert sum(e["split"] == "holdout" for e in events) == 8
    for e in events:
        assert e["event_type"] in be.EVENT_TYPES
        assert all(be._TICKER.match(t) for t in e["tickers"])
        assert e["mechanism"] and e["description"]


def test_seed_parser_rejects_unquoted_commas(tmp_path):
    good = seed_path().read_text(encoding="utf-8").splitlines()
    bad = tmp_path / "bad.csv"
    row = good[1].replace("Saffir-Simpson category", "Saffir, Simpson category", 1)
    bad.write_text("\n".join([good[0], row]) + "\n", encoding="utf-8")
    with pytest.raises(be.SeedError):
        be.parse_seed(bad)
    bad.write_text("\n".join([good[0], good[1].replace(",train,", ",trian,")]) + "\n", encoding="utf-8")
    with pytest.raises(be.SeedError, match="split"):
        be.parse_seed(bad)


def test_corpus_outcomes_are_live_and_proxies_recorded():
    events = load_events()
    assert len(events) >= 35
    outs = [o for e in events for o in e["outcomes"]]
    assert len(outs) >= 100
    assert all(o["price_source"].startswith("yfinance") and o.get("t0_date") for o in outs)
    for o in outs:
        if o["asset"].startswith("^CNX"):
            assert o["price_symbol"] != o["asset"] and "proxy_note" in o
        if o["asset"] == o["benchmark"]:
            assert o["abnormal_5d"] is None and o["beta_used"] == 1.0
    # a known real move: Abqaiq, Brent +14.6% on 2019-09-16
    abq = next(e for e in events if e["event_id"] == "abqaiq_attack_2019")
    brent = next(o for o in abq["outcomes"] if o["asset"] == "BZ=F")
    assert brent["ret_1d"] == pytest.approx(0.146, abs=0.005)
