"""
tests/test_stats.py
Tests statistical functions: weighted_quantile, similarity_weights, confidence_label, conformal interval.
"""
from __future__ import annotations
import math
import numpy as np
import pytest
from vectordb.stats import (
    weighted_quantile,
    similarity_weights,
    confidence_label,
    get_conformal_q,
)


def test_weighted_quantile_uniform_weights():
    # With uniform weights, weighted median should closely match unweighted median
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    weights = [0.2, 0.2, 0.2, 0.2, 0.2]
    med = weighted_quantile(values, weights, 0.5)
    assert pytest.approx(med, 0.1) == 30.0

    p10 = weighted_quantile(values, weights, 0.1)
    p90 = weighted_quantile(values, weights, 0.9)
    assert p10 < med < p90


def test_weighted_quantile_skewed_weights():
    # If high weight on 50, median should pull towards 50
    values = [10.0, 20.0, 50.0]
    weights = [0.05, 0.05, 0.90]
    med = weighted_quantile(values, weights, 0.5)
    assert med > 30.0


def test_weighted_quantile_empty():
    assert math.isnan(weighted_quantile([], [], 0.5))


def test_similarity_weights():
    # w_i = sim_i² / Σ sim²
    sims = [0.8, 0.6]
    w = similarity_weights(sims)
    # 0.8^2 = 0.64, 0.6^2 = 0.36, sum = 1.0
    assert pytest.approx(w[0], 0.001) == 0.64
    assert pytest.approx(w[1], 0.001) == 0.36
    assert pytest.approx(sum(w), 0.001) == 1.0


def test_similarity_weights_zeros():
    sims = [0.0, 0.0, 0.0]
    w = similarity_weights(sims)
    assert pytest.approx(w, 0.001) == [1/3, 1/3, 1/3]


def test_confidence_labels():
    # high: n >= 5 and mean_sim >= 0.75
    label, conf = confidence_label(5, 0.80)
    assert label == "high"
    assert conf == 0.8

    # medium: n >= 3 and mean_sim >= 0.6
    label, conf = confidence_label(3, 0.65)
    assert label == "medium"
    assert conf == 0.6

    # low otherwise
    label, conf = confidence_label(2, 0.90)
    assert label == "low"
    assert conf == 0.3

    # relaxation penalty (-0.1 per relaxation)
    label, conf = confidence_label(5, 0.80, filters_relaxed=1)
    assert label == "high"
    assert pytest.approx(conf, 0.01) == 0.7


def test_conformal_loader():
    q, n_calib = get_conformal_q("5d", "cyclone")
    # Should load the precomputed calibration from data/conformal_q.json
    assert q is not None
    assert q > 0
    assert n_calib > 0
