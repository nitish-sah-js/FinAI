"""weighted_quantile, similarity_weights, confidence rule, conformal q̂ (ported from db_nitr + additions)."""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

from vectordb import stats
from vectordb.stats import (confidence_label, conformal_q_from_residuals, get_conformal_q, similarity_weights,
                            weighted_quantile)


def test_weighted_quantile_uniform_weights():
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    med = weighted_quantile(values, [0.2] * 5, 0.5)
    assert med == pytest.approx(30.0)
    assert weighted_quantile(values, [0.2] * 5, 0.1) < med < weighted_quantile(values, [0.2] * 5, 0.9)


def test_weighted_quantile_skewed_weights():
    assert weighted_quantile([10.0, 20.0, 50.0], [0.05, 0.05, 0.90], 0.5) > 30.0


def test_weighted_quantile_empty():
    assert math.isnan(weighted_quantile([], [], 0.5))


def test_similarity_weights():
    w = similarity_weights([0.8, 0.6])
    assert w[0] == pytest.approx(0.64) and w[1] == pytest.approx(0.36) and sum(w) == pytest.approx(1.0)


def test_similarity_weights_zeros():
    assert similarity_weights([0.0, 0.0, 0.0]) == pytest.approx([1 / 3] * 3)


def test_confidence_labels():
    assert confidence_label(5, 0.80) == ("high", 0.8)
    assert confidence_label(3, 0.65) == ("medium", 0.6)
    assert confidence_label(2, 0.90) == ("low", 0.3)
    label, conf = confidence_label(5, 0.80, filters_relaxed=1)
    assert label == "high" and conf == pytest.approx(0.7)


def test_conformal_loader():
    q, n_calib, src = get_conformal_q("5d", "cyclone", "raw")
    assert q is not None and q > 0 and n_calib > 0 and src.startswith("cyclone")


def test_conformal_quantile_is_conservative_higher():
    res = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10]
    # n=10 → level ceil(11*0.8)/10 = 0.9 → "higher" order statistic = 0.10 (not an interpolated 0.091)
    assert conformal_q_from_residuals(res) == pytest.approx(0.10)
    assert conformal_q_from_residuals([0.5]) == pytest.approx(0.5)
    assert conformal_q_from_residuals([]) is None


def test_conformal_lookup_prefers_measure_then_type_then_all(tmp_path, monkeypatch):
    table = {"5d": {"cyclone": {"q": 0.05, "n_calib": 4, "by_measure": {"raw": {"q": 0.04, "n_calib": 3}}},
                    "_all": {"q": 0.09, "n_calib": 20, "by_measure": {"raw": {"q": 0.08, "n_calib": 12}}}}}
    p = tmp_path / "conformal_q.json"
    p.write_text(json.dumps(table))
    monkeypatch.setattr(stats, "conformal_path", lambda: p)
    assert get_conformal_q("5d", "cyclone", "raw") == (0.05, 4, "cyclone")       # by_measure n<8 skipped
    assert get_conformal_q("5d", None, "raw") == (0.08, 12, "_all/raw")
    assert get_conformal_q("5d", "heatwave", "abnormal") == (0.09, 20, "_all")
    assert get_conformal_q("1d", "cyclone", "raw") == (None, 0, None)


def test_calibration_file_counts_events_and_is_train_only():
    from vectordb.corpus import load_events
    from vectordb.paths import conformal_path
    table = json.loads(conformal_path().read_text(encoding="utf-8"))
    n_train = sum(1 for e in load_events() if e["split"] == "train")
    assert table["_meta"]["n_train"] == n_train
    for h in ("1d", "5d", "20d"):
        assert table[h]["_all"]["n_calib"] <= n_train                 # events, not residuals
        assert table[h]["_all"]["n_residuals"] >= table[h]["_all"]["n_calib"]
        assert all(np.isfinite(e["q"]) for e in table[h].values())
