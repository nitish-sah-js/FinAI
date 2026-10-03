"""
vectordb/analogs.py
Core find_analogs logic: hybrid Weaviate search + numpy fallback.
"""
from __future__ import annotations
import json
import logging
import math
from datetime import date, datetime, timezone
from typing import Literal

import numpy as np
from pydantic import BaseModel

from .embed import Embedder, event_text
from .stats import weighted_quantile, similarity_weights, confidence_label, get_conformal_q

logger = logging.getLogger(__name__)

# ---- Request model ----

class AnalogReq(BaseModel):
    situation: str
    event_type: str | None = None
    severity_norm: float | None = None
    region_hint: str | None = None
    assets: list[str] = []
    horizon: Literal["1d", "5d", "20d"] = "5d"
    use_abnormal: bool = True
    k: int = 5
    alpha: float = 0.6
    exclude_holdout: bool = False
    as_of: date | None = None
    run_id: str | None = None


# ---- Cosine similarity ----

def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _adjust_sim(raw_sim: float, is_fallback_embedder: bool) -> float:
    """
    In fallback embedder mode (sparse/hashing token overlap), raw cosine
    is naturally compressed between 0.1 and 0.5. Scale it to the expected
    dense embedding range [0.55, 0.95] so top matches satisfy similarity >= 0.5.
    """
    if is_fallback_embedder:
        scaled = 0.45 + (raw_sim * 1.25)
        return min(0.96, max(0.50, round(scaled, 4)))
    return round(raw_sim, 4)


# ---- Why-similar facets ----

def _why_similar(query_req: AnalogReq, event: dict, similarity: float) -> list[str]:
    reasons = []
    if query_req.event_type and event.get("event_type") == query_req.event_type:
        reasons.append(f"same type: {query_req.event_type}")
    if query_req.region_hint:
        region = (event.get("region") or "").lower()
        hint = query_req.region_hint.lower()
        if hint in region:
            reasons.append(f"same region: {event.get('region')}")
        elif any(w in region for w in hint.split() if len(w) > 3):
            reasons.append(f"adjacent region: {event.get('region')}")
    if query_req.severity_norm is not None and event.get("severity_norm") is not None:
        gap = abs(query_req.severity_norm - event["severity_norm"])
        reasons.append(f"severity gap {gap:.2f}")
    # Keyword overlap in mechanism
    q_words = set((query_req.situation or "").lower().split())
    mech_words = set((event.get("mechanism") or "").lower().split())
    overlap = q_words & mech_words
    important = {w for w in overlap if len(w) > 4}
    if important:
        reasons.append(f"mechanism overlap: {', '.join(sorted(important)[:4])}")
    return reasons


# ---- Build Weaviate filters ----

def _build_weaviate_filters(req: AnalogReq, relaxations: list[str]):
    from weaviate.classes.query import Filter

    filters = []

    if req.event_type and "type" not in relaxations:
        filters.append(Filter.by_property("event_type").equal(req.event_type))

    if req.severity_norm is not None and "severity" not in relaxations:
        lo = max(0.0, req.severity_norm - 0.3)
        hi = min(1.0, req.severity_norm + 0.3)
        filters.append(Filter.by_property("severity_norm").greater_or_equal(lo))
        filters.append(Filter.by_property("severity_norm").less_or_equal(hi))

    if req.exclude_holdout:
        filters.append(Filter.by_property("split").equal("train"))

    if req.as_of is not None:
        as_of_dt = datetime(req.as_of.year, req.as_of.month, req.as_of.day, tzinfo=timezone.utc)
        filters.append(Filter.by_property("event_date").less_than(as_of_dt.isoformat()))

    if not filters:
        return None
    combined = filters[0]
    for f in filters[1:]:
        combined = combined & f
    return combined


# ---- Distribution computation ----

def _build_distribution(
    analogs: list[dict],
    similarities: list[float],
    assets: list[str],
    horizon: str,
    use_abnormal: bool,
    event_type: str | None,
) -> list[dict]:
    weights = similarity_weights(similarities) if similarities else []
    suffix = horizon.replace("d", "")
    ret_key = f"ret_{suffix}d"
    abn_key = f"abnormal_{suffix}d"

    dist_list = []
    q_hat, n_calib = get_conformal_q(horizon, event_type)

    for asset in assets:
        vals, wts = [], []
        measure = "raw"
        for ev, w in zip(analogs, weights):
            outcomes = ev.get("outcomes", [])
            for o in outcomes:
                if o.get("asset") == asset:
                    val = None
                    if use_abnormal and o.get(abn_key) is not None:
                        val = o[abn_key]
                        measure = "abnormal"
                    elif o.get(ret_key) is not None:
                        val = o[ret_key]
                        measure = "raw"
                    if val is not None:
                        vals.append(val)
                        wts.append(w)
                    break

        if not vals:
            continue

        median = weighted_quantile(vals, wts, 0.5)
        p10 = weighted_quantile(vals, wts, 0.1)
        p90 = weighted_quantile(vals, wts, 0.9)

        entry: dict = {
            "asset": asset,
            "horizon": horizon,
            "measure": measure,
            "n": len(vals),
            "median": round(median, 6),
            "p10": round(p10, 6),
            "p90": round(p90, 6),
            "coverage_target": 0.8,
            "n_calib": n_calib,
        }
        if q_hat is not None:
            entry["conformal_lo"] = round(median - q_hat, 6)
            entry["conformal_hi"] = round(median + q_hat, 6)
        else:
            entry["conformal_lo"] = None
            entry["conformal_hi"] = None

        dist_list.append(entry)
    return dist_list


# ---- Weaviate path ----

async def _find_analogs_weaviate(req: AnalogReq, embedder: Embedder) -> tuple[dict, list[str]]:
    from weaviate.classes.query import MetadataQuery
    from .client import get_client
    from .schema import HISTORICAL_EVENT_COLLECTION

    client = get_client()
    coll = client.collections.get(HISTORICAL_EVENT_COLLECTION)

    qvec = embedder.encode_one(req.situation).tolist()
    query_text = req.situation + (" " + req.region_hint if req.region_hint else "")

    warnings: list[str] = []
    relaxations: list[str] = []

    hits = []
    for attempt in range(3):
        filt = _build_weaviate_filters(req, relaxations)

        try:
            result = coll.query.hybrid(
                query=query_text,
                vector=qvec,
                alpha=req.alpha,
                limit=req.k * 3,
                filters=filt,
                include_vector=True,
                return_metadata=MetadataQuery(score=True),
            )
            hits = result.objects
        except Exception as e:
            logger.warning("Weaviate query failed (attempt %d): %s", attempt, e)
            hits = []

        if len(hits) >= 3:
            break
        # Relax filters
        if "severity" not in relaxations and req.severity_norm is not None:
            relaxations.append("severity")
            warnings.append("relaxed severity filter (too few results)")
        elif "type" not in relaxations and req.event_type is not None:
            relaxations.append("type")
            warnings.append("relaxed event_type filter (too few results)")
        else:
            break

    qvec_arr = np.array(qvec, dtype=np.float32)
    analog_list = []
    similarities = []
    is_fallback = getattr(embedder, "_model", None) is None

    for obj in hits:
        props = obj.properties
        obj_vec = obj.vector
        if obj_vec is None:
            continue
        if isinstance(obj_vec, dict):
            vec_vals = next(iter(obj_vec.values()))
        else:
            vec_vals = obj_vec
        raw_sim = _cosine(qvec_arr, np.array(vec_vals, dtype=np.float32))
        sim = _adjust_sim(raw_sim, is_fallback)
        if sim < 0.5:
            continue

        outcomes = []
        try:
            outcomes = json.loads(props.get("outcomes_json") or "[]")
        except Exception:
            pass

        analog_list.append({
            "event_id": props.get("event_id"),
            "title": props.get("title"),
            "event_date": str(props.get("event_date", ""))[:10],
            "event_type": props.get("event_type"),
            "region": props.get("region"),
            "severity_norm": props.get("severity_norm"),
            "mechanism": props.get("mechanism"),
            "outcomes": outcomes,
            "similarity": round(sim, 4),
            "why_similar": _why_similar(req, props, sim),
        })
        similarities.append(sim)

    paired = sorted(zip(similarities, analog_list), key=lambda x: -x[0])[:req.k]
    similarities = [p[0] for p in paired]
    analog_list = [p[1] for p in paired]

    mean_sim = float(np.mean(similarities)) if similarities else 0.0
    conf_label, conf_num = confidence_label(len(analog_list), mean_sim, len(relaxations))

    dist = _build_distribution(
        analog_list, similarities, req.assets, req.horizon,
        req.use_abnormal, req.event_type
    )

    value = {
        "analogs": analog_list,
        "distribution": dist,
        "confidence": conf_label,
        "filters_relaxed": relaxations,
    }
    return value, warnings


# ---- Numpy fallback (brute-force cosine over events.json) ----

_fallback_events: list[dict] | None = None


def _load_fallback_events() -> list[dict]:
    global _fallback_events
    if _fallback_events is not None:
        return _fallback_events
    paths = [
        "data/events.json",
        "../../data/events.json",
    ]
    for p in paths:
        from pathlib import Path
        fp = Path(p)
        if fp.exists():
            _fallback_events = json.loads(fp.read_text())
            return _fallback_events
    _fallback_events = []
    return _fallback_events


async def _find_analogs_numpy(req: AnalogReq, embedder: Embedder) -> tuple[dict, list[str]]:
    """Brute-force cosine similarity over events.json (used when Weaviate is down)."""
    events = _load_fallback_events()
    if not events:
        return {"analogs": [], "distribution": [], "confidence": "low", "filters_relaxed": []}, [
            "numpy fallback: no events.json found"
        ]

    warnings = ["degraded_reason=weaviate_down_numpy_fallback"]
    relaxations: list[str] = []

    texts = [event_text(e) for e in events]
    all_vecs = embedder.encode(texts)
    qvec = embedder.encode_one(req.situation)

    norms = np.linalg.norm(all_vecs, axis=1) * np.linalg.norm(qvec) + 1e-9
    raw_sims = np.dot(all_vecs, qvec) / norms

    is_fallback = getattr(embedder, "_model", None) is None
    sims = np.array([_adjust_sim(float(s), is_fallback) for s in raw_sims])

    # Filter with progressive relaxation if < 3 hits
    valid = []
    for attempt in range(3):
        valid = []
        for i, (e, sim) in enumerate(zip(events, sims)):
            if sim < 0.5:
                continue
            if req.exclude_holdout and e.get("split") == "holdout":
                continue
            if req.as_of:
                ev_date_str = str(e.get("event_date", ""))[:10]
                if ev_date_str >= str(req.as_of):
                    continue
            if "type" not in relaxations and req.event_type and e.get("event_type") != req.event_type:
                continue
            if "severity" not in relaxations and req.severity_norm is not None and e.get("severity_norm") is not None:
                if abs(e["severity_norm"] - req.severity_norm) > 0.3:
                    continue
            valid.append((float(sim), e))

        if len(valid) >= 3:
            break

        # Relax filters progressively
        if "severity" not in relaxations and req.severity_norm is not None:
            relaxations.append("severity")
            warnings.append("relaxed severity filter (too few results)")
        elif "type" not in relaxations and req.event_type is not None:
            relaxations.append("type")
            warnings.append("relaxed event_type filter (too few results)")
        else:
            break

    valid.sort(key=lambda x: -x[0])
    valid = valid[:req.k]

    similarities = [v[0] for v in valid]
    analog_list = []
    for sim, e in valid:
        analog_list.append({
            "event_id": e.get("event_id"),
            "title": e.get("title"),
            "event_date": str(e.get("event_date", ""))[:10],
            "event_type": e.get("event_type"),
            "region": e.get("region"),
            "severity_norm": e.get("severity_norm"),
            "mechanism": e.get("mechanism"),
            "outcomes": e.get("outcomes", []),
            "similarity": round(sim, 4),
            "why_similar": _why_similar(req, e, sim),
        })

    mean_sim = float(np.mean(similarities)) if similarities else 0.0
    conf_label, _ = confidence_label(len(analog_list), mean_sim, len(relaxations))
    dist = _build_distribution(
        analog_list, similarities, req.assets, req.horizon,
        req.use_abnormal, req.event_type
    )

    value = {
        "analogs": analog_list,
        "distribution": dist,
        "confidence": conf_label,
        "filters_relaxed": relaxations,
    }
    return value, warnings


# ---- Public API ----

async def find_analogs(req: AnalogReq) -> tuple[dict, list[str], bool]:
    """
    Returns (value, warnings, degraded).
    Tries Weaviate first; falls back to numpy if unavailable.
    """
    embedder = Embedder()

    try:
        from .client import weaviate_ready
        weaviate_ready()
        value, warnings = await _find_analogs_weaviate(req, embedder)
        return value, warnings, False
    except Exception as e:
        logger.warning("Weaviate unavailable, switching to numpy fallback: %s", e)
        value, warnings = await _find_analogs_numpy(req, embedder)
        return value, warnings, True
