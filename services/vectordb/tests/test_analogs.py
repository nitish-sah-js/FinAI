"""find_analogs: fallback search, holdout/as_of hard filters, distribution rules, nonsense guard, hashing fallback."""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone

import numpy as np
import pytest

from vectordb import analogs as A
from vectordb.analogs import AnalogReq, Criteria, build_distribution, find_analogs, passes_hard_filters
from vectordb.assets import FACTOR_ASSETS
from vectordb.corpus import HOLDOUT_IDS, holdout_ids, load_events
from vectordb.embed import Embedder, hashing_encode
from vectordb.paths import holdout_path

CYCLONE = ("cyclone Odisha severity 6 IMD class (1-7). Very severe cyclonic storm making landfall on Odisha coast "
           "near Puri with extreme winds and flooding. Mechanism: port shutdowns, power outages, crop damage")
HURRICANE = ("hurricane Gulf of Mexico severity 4 Saffir-Simpson category. Category 4 hurricane Gulf of Mexico refinery "
             "coast landfall. Mechanism: refinery shutdowns, offshore oil platform shut-ins, pipeline outages")


def run(req: AnalogReq):
    return asyncio.run(find_analogs(req))


# ---------------------------------------------------------------- ported from db_nitr
def test_find_analogs_numpy_fallback_general(embedder):
    val, warnings, meta = run(AnalogReq(situation=CYCLONE, event_type="cyclone", severity_norm=0.71,
                                        region_hint="Odisha", assets=["^NSEI", "^CNXFMCG"]))
    assert meta.backend == "numpy" and meta.degraded and meta.reason == "weaviate_down_numpy_fallback"
    assert val["analogs"]
    for a in val["analogs"]:
        assert a["similarity"] >= 0.5 and a["event_id"] and isinstance(a["why_similar"], list)


def test_holdout_filter_never_returns_holdouts(embedder):
    val, _, _ = run(AnalogReq(situation="cyclone Gujarat Kutch severity 5 IMD class. Severe cyclonic storm tracking "
                              "toward Gujarat coast. Mechanism: port shutdown, oil pipeline disruption",
                              event_type="cyclone", assets=["ADANIPORTS.NS", "^NSEI"], k=10, exclude_holdout=True))
    assert not {a["event_id"] for a in val["analogs"]} & HOLDOUT_IDS


def test_as_of_filter_no_lookahead(embedder):
    val, _, _ = run(AnalogReq(situation="cyclone Bay of Bengal severity 6. Mechanism: port damage and flooding",
                              event_type="cyclone", as_of=date(2020, 1, 1), k=10))
    assert val["analogs"]
    assert all(a["event_date"] < "2020-01-01" for a in val["analogs"])


def test_cyclone_odisha_top_matches(embedder):
    val, _, _ = run(AnalogReq(situation=CYCLONE, event_type="cyclone", region_hint="Odisha", assets=["^NSEI"]))
    top3 = [a["event_id"] for a in val["analogs"][:3]]
    assert {"cyclone_fani_2019", "cyclone_phailin_2013", "cyclone_yaas_2021"} & set(top3), top3


def test_hurricane_gulf_top_matches(embedder):
    val, _, _ = run(AnalogReq(situation=HURRICANE, event_type="hurricane", region_hint="Gulf of Mexico",
                              assets=["CL=F", "NG=F"]))
    top3 = [a["event_id"] for a in val["analogs"][:3]]
    assert all(t.startswith("hurricane_") for t in top3)
    # acceptance (08 §10) names Harvey/Laura/Ida; Katrina/Rita are equally Gulf-refinery events in this corpus
    assert {"hurricane_harvey_2017", "hurricane_laura_2020", "hurricane_ida_2021",
            "hurricane_katrina_2005", "hurricane_rita_2005"} & set(top3), top3


def test_hurricane_untyped_query_still_finds_hurricanes(embedder):
    val, _, _ = run(AnalogReq(situation="Category 4 hurricane Gulf of Mexico refinery coast", assets=["CL=F"]))
    assert all(a["event_type"] == "hurricane" for a in val["analogs"][:3])


# ---------------------------------------------------------------- holdout leakage (all 8 ids, every type)
def test_holdout_file_matches_corpus():
    """data/events.json (backtest) ids/dates must exist in the corpus as split=holdout with the same date."""
    recs = json.loads(holdout_path().read_text(encoding="utf-8"))
    corpus = {e["event_id"]: e for e in load_events()}
    assert {r["event_id"] for r in recs} == set(HOLDOUT_IDS) == set(holdout_ids())
    for r in recs:
        e = corpus[r["event_id"]]
        assert e["split"] == "holdout" and e["event_date"][:10] == r["event_date"][:10]


@pytest.mark.parametrize("hid", sorted(HOLDOUT_IDS))
@pytest.mark.parametrize("etype", [None, *A.EVENT_TYPES])
def test_exclude_holdout_never_returns_any_holdout(embedder, hid, etype):
    """Query with the held-out event's OWN text (its best possible match) under every type filter."""
    ev = next(e for e in load_events() if e["event_id"] == hid)
    val, _, _ = run(AnalogReq(situation=ev["embedding_text"], event_type=etype, severity_norm=ev["severity_norm"],
                              region_hint=ev["region"], assets=ev["tickers"], k=20, exclude_holdout=True))
    got = {a["event_id"] for a in val["analogs"]}
    assert not got & HOLDOUT_IDS, got & HOLDOUT_IDS
    assert all(a["split"] == "train" for a in val["analogs"])


def test_holdout_text_without_exclusion_finds_itself(embedder):
    """Control: the same query WITHOUT exclude_holdout returns the event, so the test above is meaningful."""
    ev = next(e for e in load_events() if e["event_id"] == "abqaiq_attack_2019")
    val, _, _ = run(AnalogReq(situation=ev["embedding_text"], assets=ev["tickers"]))
    assert val["analogs"][0]["event_id"] == "abqaiq_attack_2019"


def test_hard_filter_rejects_holdout_id_even_if_marked_train():
    """The old bug: only split=='holdout' was excluded. An id from data/events.json must be dropped regardless."""
    c = Criteria(exclude_holdout=True, holdout=holdout_ids())
    assert not passes_hard_filters({"event_id": "abqaiq_attack_2019", "split": "train", "event_date": "2019-09-16"}, c)
    assert not passes_hard_filters({"event_id": "x", "split": "", "event_date": "2019-09-16"}, c)     # unknown split
    assert passes_hard_filters({"event_id": "x", "split": "train", "event_date": "2019-09-16"}, c)


# ---------------------------------------------------------------- as_of (time machine)
@pytest.mark.parametrize("as_of", [date(2021, 8, 29), datetime(2021, 8, 29, 15, 0, tzinfo=timezone.utc),
                                   "2021-08-29", "2021-08-29T23:00:00Z"])
def test_as_of_excludes_events_on_or_after(embedder, as_of):
    req = AnalogReq(situation=HURRICANE, event_type="hurricane", assets=["CL=F", "NG=F"], k=10, as_of=as_of)
    assert req.as_of == date(2021, 8, 29)
    val, _, _ = run(req)
    ids = [a["event_id"] for a in val["analogs"]]
    assert ids and "hurricane_ida_2021" not in ids                    # dated ON as_of → excluded
    assert all(a["event_date"] < "2021-08-29" for a in val["analogs"])


def test_as_of_drops_outcomes_not_yet_realised():
    """An analog dated before as_of whose 20d window ends after as_of must not feed the distribution."""
    analog = {"event_id": "e1", "similarity": 0.9, "outcomes": [
        {"asset": "CL=F", "ret_5d": 0.05, "ret_20d": 0.10, "abnormal_5d": 0.04, "abnormal_20d": 0.08,
         "realized_dates": {"5d": "2021-08-20", "20d": "2021-09-10"}}]}
    d5, _ = build_distribution([analog], ["CL=F"], "5d", True, None, as_of=date(2021, 8, 29))
    d20, w = build_distribution([analog], ["CL=F"], "20d", True, None, as_of=date(2021, 8, 29))
    assert d5 and d5[0]["n"] == 1 and not d20
    assert any("as_of" in x for x in w)


# ---------------------------------------------------------------- distribution rules
def test_one_measure_per_asset_and_raw_for_factor_assets(embedder):
    assets = ["^NSEI", "CL=F", "BZ=F", "INR=X", "ONGC.NS", "BPCL.NS", "^NSEBANK"]
    for situation, et in [(CYCLONE, "cyclone"), (HURRICANE, "hurricane"),
                          ("oil_shock Global severity 8 oil move %. Brent spikes after a supply shock. Mechanism: "
                           "crude supply outage, refiner margins, rupee pressure", "oil_shock")]:
        val, _, _ = run(AnalogReq(situation=situation, event_type=et, assets=assets, use_abnormal=True))
        per_asset = {}
        for d in val["distribution"]:
            per_asset.setdefault(d["asset"], set()).add(d["measure"])
        assert all(len(m) == 1 for m in per_asset.values()), per_asset
        for f in FACTOR_ASSETS & per_asset.keys():
            assert per_asset[f] == {"raw"}
        for d in val["distribution"]:
            for key in ("asset", "horizon", "measure", "n", "median", "p10", "p90", "conformal_lo", "conformal_hi",
                        "coverage_target", "n_calib"):
                assert key in d
            assert d["p10"] <= d["median"] <= d["p90"]


def test_mixed_measures_fall_back_to_raw_for_the_whole_asset():
    analogs = [{"event_id": "a", "similarity": 0.9, "outcomes": [{"asset": "X.NS", "ret_5d": 0.02, "abnormal_5d": 0.01}]},
               {"event_id": "b", "similarity": 0.8, "outcomes": [{"asset": "X.NS", "ret_5d": -0.03, "abnormal_5d": None}]}]
    d, _ = build_distribution(analogs, ["X.NS"], "5d", True, None)
    assert d[0]["measure"] == "raw" and d[0]["n"] == 2 and "measure_note" in d[0]
    analogs[1]["outcomes"][0]["abnormal_5d"] = -0.02
    d, _ = build_distribution(analogs, ["X.NS"], "5d", True, None)
    assert d[0]["measure"] == "abnormal"


def test_proxy_group_distribution(embedder):
    """ITC.NS appears in few cyclone analogs; FMCG peers (^CNXFMCG via HINDUNILVR.NS) stand in (08 §6.6)."""
    val, _, _ = run(AnalogReq(situation=CYCLONE, event_type="cyclone", region_hint="Odisha", assets=["ITC.NS"]))
    d = val["distribution"][0]
    assert d["asset"] == "ITC.NS" and d["proxy_group"] == "in_fmcg" and d["n"] > d["n_exact"]
    assert "^CNXFMCG" in d["proxies_used"]


def test_event_type_aliases_from_intent():
    assert AnalogReq(situation="x", event_type="oil").event_type == "oil_shock"
    assert AnalogReq(situation="x", event_type="rates").event_type == "rate_shock"
    assert AnalogReq(situation="x", event_type="monsoon").event_type == "monsoon_deficit"
    assert AnalogReq(situation="x", event_type="other").event_type is None


# ---------------------------------------------------------------- nonsense / degraded honesty
@pytest.mark.parametrize("q", ["banana smoothie recipe with chocolate and almond milk", "asdf qwerty zxcv",
                               "lorem ipsum dolor sit amet", "my cat likes to sleep on the sofa all afternoon"])
def test_nonsense_query_returns_no_analogs(embedder, q):
    val, warnings, _ = run(AnalogReq(situation=q, assets=["^NSEI"]))
    assert len(val["analogs"]) == 0 and val["distribution"] == [] and val["confidence"] == "low"
    assert warnings


def test_hashing_fallback_is_honest(monkeypatch):
    """No model → raw hashing cosine with its own threshold, degraded, low confidence; nonsense matches nothing."""
    fb = Embedder(load=False)
    monkeypatch.setattr(fb, "_model", None)
    assert fb.is_fallback
    val, warnings, meta = asyncio.run(find_analogs(AnalogReq(situation="zebra quantum violin xylophone"), fb))
    assert meta.degraded and meta.reason == "embedder_unavailable_hashing_fallback"
    assert val["analogs"] == [] and val["min_similarity"] == A.HASH_MIN_SIM
    assert any("hashing" in w for w in warnings)
    val, _, meta = asyncio.run(find_analogs(AnalogReq(situation=CYCLONE, event_type="cyclone"), fb))
    assert val["analogs"]
    # similarities are the RAW hashing cosine (no clamp to ≥ 0.5): they equal the dot product of hashing vectors
    ev = next(e for e in load_events() if e["event_id"] == val["analogs"][0]["event_id"])
    raw = float(hashing_encode([CYCLONE])[0] @ hashing_encode([ev["embedding_text"]])[0])
    assert val["analogs"][0]["similarity"] == pytest.approx(raw, abs=1e-3)
    assert A.evidence_confidence(val, meta) <= 0.2


def test_relaxation_is_recorded(embedder):
    # heatwave with an extreme severity band leaves < 3 → severity relaxed first, then type
    val, warnings, _ = run(AnalogReq(situation="heatwave North India severity 6 anomaly deg C. Record heat. "
                                     "Mechanism: power demand", event_type="heatwave", severity_norm=0.0, k=5))
    assert val["filters_relaxed"][0] == "severity"
    assert any(w.startswith("relaxed severity") for w in warnings)


def test_weaviate_filter_objects_build():
    """The Weaviate filter builder is untested against a server; at least check it builds for every combination."""
    c = Criteria(event_type="cyclone", severity_norm=0.7, exclude_holdout=True, as_of=date(2022, 1, 1),
                 holdout=holdout_ids())
    for step in A.relaxation_steps(c):
        assert A._weaviate_filters(c, step) is not None
    assert A._weaviate_filters(Criteria(), []) is None
