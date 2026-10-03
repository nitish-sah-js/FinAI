"""Leave-one-out split-conformal calibration (08 §6.7) → data/conformal_q.json.

For every split=="train" event (the target), its analogs are found with EXACTLY the service's rules
(vectordb.analogs.select_analogs + assemble_value): same type filter, severity ± 0.3, relaxation order, similarity
≥ 0.5, k = 5, train-only candidates (exclude_holdout) and the time-machine rule as_of = target event_date (only
earlier events, and only outcomes realised before it). The prediction is the weighted median of the analog
distribution for each of the target's assets, in the measure the service would report; residual = |actual − median|.

q̂ = quantile(residuals, ceil((n+1)·0.8)/n, method="higher") per horizon × event_type (and × measure), plus "_all".
n_calib = number of target EVENTS with ≥ 1 residual; n_residuals = number of (event, asset) residuals.

    cd services/vectordb && ../../.venv/Scripts/python scripts/calibrate_conformal.py
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vectordb.analogs import AnalogReq, Criteria, MIN_SIM, assemble_value, select_analogs   # noqa: E402
from vectordb.corpus import corpus_vectors, holdout_ids, load_events                         # noqa: E402
from vectordb.embed import Embedder                                                          # noqa: E402
from vectordb.paths import conformal_path, corpus_path                                       # noqa: E402
from vectordb.stats import COVERAGE, conformal_q_from_residuals                              # noqa: E402

logger = logging.getLogger("calibrate_conformal")
HORIZONS = ("1d", "5d", "20d")


def loo_residuals(events: list[dict], vecs, horizon: str, k: int = 5) -> list[dict]:
    """[{event_id, event_type, asset, measure, residual}] for every train target with a prediction."""
    hold = holdout_ids()
    out = []
    for i, tgt in enumerate(events):
        if tgt.get("split") != "train" or tgt.get("event_id") in hold:
            continue
        as_of = date.fromisoformat(str(tgt["event_date"])[:10])
        assets = [o["asset"] for o in tgt.get("outcomes") or []]
        if not assets:
            continue
        req = AnalogReq(situation=tgt.get("embedding_text") or tgt["title"], event_type=tgt["event_type"],
                        severity_norm=tgt.get("severity_norm"), assets=assets, horizon=horizon, k=k,
                        exclude_holdout=True, as_of=as_of)
        c = Criteria.from_req(req, MIN_SIM)
        c.exclude_ids = frozenset({tgt["event_id"]})
        chosen, relaxed, _ = select_analogs(events, vecs @ vecs[i], c)
        value, _ = assemble_value(chosen, relaxed, req)
        actual = {o["asset"]: o for o in tgt["outcomes"]}
        for d in value["distribution"]:
            o = actual.get(d["asset"])
            key = f"ret_{horizon}" if d["measure"] == "raw" else f"abnormal_{horizon}"
            if o is None or o.get(key) is None:
                continue
            out.append({"event_id": tgt["event_id"], "event_type": tgt["event_type"], "asset": d["asset"],
                        "measure": d["measure"], "residual": abs(float(o[key]) - float(d["median"])),
                        "n_analogs": d["n"], "relaxed": relaxed})
    return out


def _entry(rows: list[dict], coverage: float) -> dict:
    res = [r["residual"] for r in rows]
    return {"q": None if not res else round(conformal_q_from_residuals(res, coverage), 6),
            "n_calib": len({r["event_id"] for r in rows}), "n_residuals": len(res), "coverage_target": coverage}


def calibrate(events_path: Path, out_path: Path, k: int = 5, coverage: float = COVERAGE) -> dict:
    events = load_events(events_path)
    train = [e for e in events if e.get("split") == "train"]
    if len(train) < 3:
        raise SystemExit(f"only {len(train)} train events in {events_path}")
    emb = Embedder()
    if emb.is_fallback:
        raise SystemExit("bge-small-en-v1.5 is not available; refusing to calibrate on the hashing fallback")
    vecs = corpus_vectors(events, emb)
    table: dict = {"_meta": {"built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                             "corpus": events_path.name, "n_train": len(train), "k": k, "min_similarity": MIN_SIM,
                             "embedder": emb.name, "method": "leave-one-out, service selection rules, "
                             "as_of = target event_date, np.quantile(method='higher')"}}
    for h in HORIZONS:
        rows = loo_residuals(events, vecs, h, k)
        by_type = defaultdict(list)
        for r in rows:
            by_type[r["event_type"]].append(r)
        table[h] = {}
        for t, rs in sorted(by_type.items()) + [("_all", rows)]:
            e = _entry(rs, coverage)
            bm = defaultdict(list)
            for r in rs:
                bm[r["measure"]].append(r)
            e["by_measure"] = {m: _entry(v, coverage) for m, v in sorted(bm.items())}
            table[h][t] = e
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(table, indent=1), encoding="utf-8")
    logger.info("wrote %s", out_path)
    return table


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    t = calibrate(a.events or corpus_path(), a.out or conformal_path())
    for h in HORIZONS:
        for typ, e in t[h].items():
            print(f"{h:>3} {typ:16} q={e['q']} n_calib={e['n_calib']} n_res={e['n_residuals']} "
                  + " ".join(f"{m}:q={v['q']},n={v['n_calib']}" for m, v in e["by_measure"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
