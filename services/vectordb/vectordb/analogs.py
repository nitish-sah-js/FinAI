"""find_analogs (08 §6): Weaviate hybrid search, or brute-force cosine in numpy when Weaviate is down.

Both backends share the same pure selection rules (`passes_hard_filters`, `select_analogs`) and the same distribution
builder, and scripts/calibrate_conformal.py reuses them for leave-one-out, so the calibration matches what the
service does.

Hard filters (never relaxed):
  * exclude_holdout → split == "train" AND event_id ∉ holdout ids (08 §4.2 list ∪ backtest data/events.json)
  * as_of           → event_date < as_of (time machine); an analog's outcome is only used if its horizon window
                      also closed before as_of
Soft filters (relaxed when < 3 analogs survive, severity first, then type): event_type, severity_norm ± 0.3.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import client as wv
from .assets import FACTOR_ASSETS, group_of
from .corpus import corpus_vectors, holdout_ids, load_events
from .embed import Embedder
from .stats import COVERAGE, MIN_CALIB, confidence_label, get_conformal_q, similarity_weights, weighted_quantile

logger = logging.getLogger(__name__)

MIN_SIM = 0.5            # 08 §6.4, bge-small cosine
HASH_MIN_SIM = 0.2       # raw cosine of the hashing fallback (different scale, no rescaling)
SEV_BAND = 0.3
MIN_HITS = 3
# Query specificity guard: bge-small cosines are compressed (unrelated text still scores ~0.45-0.55 against
# everything). A query whose best match is barely above the corpus median matches nothing in particular, so it gets
# no analogs. Measured 2026-10-03 on the 40-event corpus: real queries top-median >= 0.129, nonsense <= 0.088.
MIN_CONTRAST = 0.10

EVENT_TYPES = ("hurricane", "cyclone", "monsoon_deficit", "heatwave", "rate_shock", "oil_shock", "policy")
# Intent.event_type (01 §5) uses short names; map them onto the corpus types.
EVENT_TYPE_ALIASES = {
    "monsoon": "monsoon_deficit", "drought": "monsoon_deficit", "monsoon_deficit": "monsoon_deficit",
    "rates": "rate_shock", "rate": "rate_shock", "rate_shock": "rate_shock",
    "oil": "oil_shock", "oil_shock": "oil_shock", "heat": "heatwave", "heatwave": "heatwave",
    "hurricane": "hurricane", "cyclone": "cyclone", "typhoon": "cyclone", "policy": "policy",
    "other": None, "market event": None, "": None,
}


# ---------------------------------------------------------------- request
class AnalogReq(BaseModel):
    model_config = ConfigDict(extra="ignore")

    situation: str
    event_type: str | None = None
    severity_norm: float | None = Field(None, ge=0, le=1)
    region_hint: str | None = None
    assets: list[str] = []
    horizon: Literal["1d", "5d", "20d"] = "5d"
    use_abnormal: bool = True
    k: int = Field(5, ge=1, le=20)
    alpha: float = Field(0.6, ge=0, le=1)
    exclude_holdout: bool = False
    as_of: date | None = None
    run_id: str | None = None
    chaos: dict[str, Any] = {}          # sent by the orchestrator's tools_client

    @field_validator("as_of", mode="before")
    @classmethod
    def _as_of_date(cls, v: Any) -> Any:
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, str) and v.strip():
            s = v.strip()
            if len(s) > 10:
                return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
            return date.fromisoformat(s)
        if v == "":
            return None
        return v

    @field_validator("event_type", mode="before")
    @classmethod
    def _event_type(cls, v: Any) -> Any:
        if v is None:
            return None
        s = str(v).strip().lower()
        return EVENT_TYPE_ALIASES.get(s, s)

    @field_validator("assets", mode="before")
    @classmethod
    def _assets(cls, v: Any) -> Any:
        if v is None:
            return []
        seen, out = set(), []
        for a in v:
            a = str(a).strip()
            if a and a not in seen:
                seen.add(a)
                out.append(a)
        return out

    @field_validator("chaos", mode="before")
    @classmethod
    def _chaos(cls, v: Any) -> Any:
        return v or {}


# ---------------------------------------------------------------- pure selection rules
@dataclass
class Criteria:
    event_type: str | None = None
    severity_norm: float | None = None
    exclude_holdout: bool = False
    as_of: date | None = None
    holdout: frozenset[str] = frozenset()
    exclude_ids: frozenset[str] = frozenset()
    k: int = 5
    min_sim: float = MIN_SIM
    min_contrast: float = MIN_CONTRAST

    @classmethod
    def from_req(cls, req: AnalogReq, min_sim: float = MIN_SIM, min_contrast: float = MIN_CONTRAST) -> "Criteria":
        return cls(event_type=req.event_type, severity_norm=req.severity_norm, exclude_holdout=req.exclude_holdout,
                   as_of=req.as_of, holdout=holdout_ids() if req.exclude_holdout else frozenset(), k=req.k,
                   min_sim=min_sim, min_contrast=min_contrast)


def specificity(all_sims: np.ndarray) -> float:
    """top − median cosine of the query against the WHOLE corpus (independent of filters)."""
    s = np.asarray(all_sims, dtype=float)
    return 0.0 if s.size < 2 else float(s.max() - np.median(s))


def unspecific_warning(contrast: float, c: "Criteria") -> str | None:
    if c.min_contrast > 0 and contrast < c.min_contrast:
        return (f"query is not specific to any past event (top-median similarity {contrast:.3f} < {c.min_contrast}); "
                "no analogs returned")
    return None


def _d10(x: Any) -> str:
    return str(x or "")[:10]


def passes_hard_filters(e: dict, c: Criteria) -> bool:
    if e.get("event_id") in c.exclude_ids:
        return False
    if c.exclude_holdout and (e.get("split") != "train" or e.get("event_id") in c.holdout):
        return False
    if c.as_of is not None and not (_d10(e.get("event_date")) and _d10(e.get("event_date")) < c.as_of.isoformat()):
        return False
    return True


def passes_soft_filters(e: dict, c: Criteria, relaxed: list[str]) -> bool:
    if c.event_type and "type" not in relaxed and e.get("event_type") != c.event_type:
        return False
    if c.severity_norm is not None and "severity" not in relaxed and e.get("severity_norm") is not None:
        if abs(float(e["severity_norm"]) - c.severity_norm) > SEV_BAND + 1e-9:
            return False
    return True


def relaxation_steps(c: Criteria) -> list[list[str]]:
    steps = [[]]
    if c.severity_norm is not None:
        steps.append(["severity"])
    if c.event_type:
        steps.append(steps[-1] + ["type"])
    return steps


def select_analogs(events: list[dict], sims: np.ndarray, c: Criteria
                   ) -> tuple[list[tuple[float, dict]], list[str], str | None]:
    """Top-k (similarity, event) with similarity ≥ min_sim; relaxes soft filters until ≥ 3 survive.
    Returns (chosen, relaxed, note); note is set when the specificity guard rejected the query."""
    note = unspecific_warning(specificity(sims), c)
    if note:
        return [], [], note
    hard = [(float(s), e) for s, e in zip(sims, events) if passes_hard_filters(e, c) and float(s) >= c.min_sim]
    need = min(MIN_HITS, c.k)
    chosen, relaxed = [], []
    for step in relaxation_steps(c):
        relaxed = step
        chosen = [(s, e) for s, e in hard if passes_soft_filters(e, c, step)]
        if len(chosen) >= need:
            break
    chosen.sort(key=lambda p: (-p[0], p[1].get("event_id", "")))
    return chosen[: c.k], relaxed, None


# ---------------------------------------------------------------- why_similar
_BASINS = {
    "bay of bengal": ("odisha", "west bengal", "andhra", "vizag", "tamil nadu", "chennai", "wb", "bengal"),
    "arabian sea": ("gujarat", "kutch", "maharashtra", "mumbai", "kerala", "bombay", "goa", "karnataka"),
    "gulf of mexico": ("gulf", "texas", "louisiana", "mississippi", "alabama"),
    "florida": ("florida",),
}
_STOP = {"with", "from", "that", "this", "were", "into", "over", "after", "while", "along", "their", "which",
         "expected", "severity", "mechanism", "across", "caused", "major", "heavy", "about", "there", "these"}


def _basin(text: str) -> str | None:
    t = (text or "").lower()
    for b, keys in _BASINS.items():
        if b in t or any(k in t for k in keys):
            return b
    return None


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (text or "").lower()) if len(w) > 3 and w not in _STOP}


def _stem(w: str) -> str:
    for suf in ("ages", "ings", "ing", "ies", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def why_similar(req: AnalogReq, e: dict, region_hint: str | None = None) -> list[str]:
    out = []
    if req.event_type and e.get("event_type") == req.event_type:
        out.append(f"same type: {req.event_type}")
    hint = (region_hint or req.region_hint or "").lower().strip()
    region = (e.get("region") or "")
    if hint:
        r = region.lower()
        if hint in r or (r and r in hint):
            out.append(f"same region: {region}")
        elif _words(hint) & _words(r):
            out.append(f"overlapping region: {region}")
        elif _basin(hint) and _basin(hint) == _basin(r):
            out.append(f"same basin: {_basin(r)}")
    if req.severity_norm is not None and e.get("severity_norm") is not None:
        out.append(f"severity gap {abs(req.severity_norm - float(e['severity_norm'])):.2f}")
    q = {_stem(w) for w in _words(req.situation)}
    mech = {_stem(w): w for w in _words(e.get("mechanism", "") + " " + e.get("description", ""))}
    common = sorted(mech[s] for s in q & set(mech))
    if common:
        out.append("mechanism overlap: " + ", ".join(common[:4]))
    return out


# ---------------------------------------------------------------- distribution
def _match_outcome(e: dict, asset: str) -> tuple[dict | None, str | None]:
    """Exact ticker first (asset or the symbol its prices came from), else a same-group proxy (08 §6.6)."""
    outs = e.get("outcomes") or []
    for o in outs:
        if o.get("asset") == asset:
            return o, None
    for o in outs:
        if o.get("price_symbol") == asset:
            return o, None
    g = group_of(asset)
    if g:
        for o in outs:
            if group_of(o.get("asset", "")) == g or group_of(o.get("price_symbol") or "") == g:
                return o, o.get("asset")
    return None, None


def _realized_before(o: dict, horizon: str, as_of: date | None) -> bool:
    if as_of is None:
        return True
    end = _d10((o.get("realized_dates") or {}).get(horizon))
    return bool(end) and end < as_of.isoformat()


def build_distribution(analogs: list[dict], assets: list[str], horizon: str, use_abnormal: bool,
                       event_type: str | None, as_of: date | None = None) -> tuple[list[dict], list[str]]:
    """One entry per asset, ONE measure per asset: raw for factor assets (quant reads only raw for those);
    otherwise abnormal when requested and every contributing analog has it, else raw."""
    ret_key, abn_key = f"ret_{horizon}", f"abnormal_{horizon}"
    dist, warnings = [], []
    lookahead_dropped = 0
    for asset in assets:
        rows = []          # (similarity, raw, abnormal, proxy_asset)
        for a in analogs:
            o, proxy = _match_outcome(a, asset)
            if o is None or o.get(ret_key) is None:
                continue
            if not _realized_before(o, horizon, as_of):
                lookahead_dropped += 1
                continue
            rows.append((float(a["similarity"]), float(o[ret_key]),
                         None if o.get(abn_key) is None else float(o[abn_key]), proxy))
        if not rows:
            continue
        if asset in FACTOR_ASSETS or not use_abnormal:
            measure = "raw"
        else:
            measure = "abnormal" if all(r[2] is not None for r in rows) else "raw"
        vals = [r[1] if measure == "raw" else r[2] for r in rows]
        w = similarity_weights([r[0] for r in rows])
        med = weighted_quantile(vals, w, 0.5)
        q_hat, n_calib, q_src = get_conformal_q(horizon, event_type, measure)
        entry = {
            "asset": asset, "horizon": horizon, "measure": measure, "n": len(rows),
            "median": round(med, 6),
            "p10": round(weighted_quantile(vals, w, 0.1), 6),
            "p90": round(weighted_quantile(vals, w, 0.9), 6),
            "conformal_lo": None if q_hat is None else round(med - q_hat, 6),
            "conformal_hi": None if q_hat is None else round(med + q_hat, 6),
            "coverage_target": COVERAGE, "n_calib": n_calib,
        }
        proxies = sorted({r[3] for r in rows if r[3]})
        if proxies:
            entry["n_exact"] = sum(1 for r in rows if not r[3])
            entry["proxy_group"] = group_of(asset)
            entry["proxies_used"] = proxies
        if q_src:
            entry["conformal_source"] = q_src
        if measure == "raw" and use_abnormal and asset not in FACTOR_ASSETS:
            entry["measure_note"] = "abnormal return unavailable for at least one analog; raw returns used"
        if q_hat is None:
            warnings.append(f"no conformal calibration for {horizon}/{event_type or '_all'}: {asset} interval omitted")
        elif n_calib < MIN_CALIB:
            warnings.append(f"conformal interval unreliable (n<{MIN_CALIB}): {asset} {horizon} "
                            f"({q_src}, n_calib={n_calib})")
        dist.append(entry)
    if lookahead_dropped:
        warnings.append(f"{lookahead_dropped} analog outcome(s) whose {horizon} window ends on/after as_of were not used")
    return dist, warnings


# ---------------------------------------------------------------- assembling a value
def _analog_view(sim: float, e: dict, req: AnalogReq) -> dict:
    wanted = set(req.assets)
    outs = e.get("outcomes") or []
    if wanted:
        outs = [o for o in outs if o.get("asset") in wanted or o.get("price_symbol") in wanted
                or (group_of(o.get("asset", "")) and group_of(o.get("asset", "")) in {group_of(a) for a in wanted})]
    return {
        "event_id": e.get("event_id"), "title": e.get("title"), "event_type": e.get("event_type"),
        "event_date": _d10(e.get("event_date")), "region": e.get("region"),
        "severity_norm": e.get("severity_norm"), "split": e.get("split"),
        "similarity": round(float(sim), 4), "why_similar": why_similar(req, e),
        "outcomes": outs,
    }


def assemble_value(chosen: list[tuple[float, dict]], relaxed: list[str], req: AnalogReq) -> tuple[dict, list[str]]:
    analogs = [_analog_view(s, e, req) for s, e in chosen]
    dist, warnings = build_distribution(analogs, req.assets, req.horizon, req.use_abnormal, req.event_type, req.as_of)
    sims = [a["similarity"] for a in analogs]
    label, _ = confidence_label(len(analogs), float(np.mean(sims)) if sims else 0.0, len(relaxed))
    for r in relaxed:
        warnings.insert(0, f"relaxed {'severity' if r == 'severity' else 'event_type'} filter (fewer than {MIN_HITS} analogs)")
    return {"analogs": analogs, "distribution": dist, "confidence": label, "filters_relaxed": list(relaxed)}, warnings


# ---------------------------------------------------------------- backends
@dataclass
class SearchMeta:
    backend: str = "numpy"
    degraded: bool = False
    reason: str | None = None
    embedder: str = ""
    n_corpus: int = 0
    min_sim: float = MIN_SIM
    notes: list[str] = field(default_factory=list)


def numpy_search(req: AnalogReq, embedder: Embedder, events: list[dict] | None = None,
                 vecs: np.ndarray | None = None) -> tuple[list[tuple[float, dict]], list[str], float, str | None]:
    events = load_events() if events is None else events
    if not events:
        return [], [], MIN_SIM, None
    vecs = corpus_vectors(events, embedder) if vecs is None else vecs
    qvec = embedder.encode_one(req.situation)
    sims = vecs @ qvec          # all vectors are L2-normalised
    if embedder.is_fallback:    # different similarity scale: own threshold, no bge contrast guard
        c = Criteria.from_req(req, HASH_MIN_SIM, 0.0)
    else:
        c = Criteria.from_req(req)
    chosen, relaxed, note = select_analogs(events, sims, c)
    return chosen, relaxed, c.min_sim, note


def _weaviate_filters(c: Criteria, relaxed: list[str]):
    from weaviate.classes.query import Filter
    fs = []
    if c.event_type and "type" not in relaxed:
        fs.append(Filter.by_property("event_type").equal(c.event_type))
    if c.severity_norm is not None and "severity" not in relaxed:
        fs.append(Filter.by_property("severity_norm").greater_or_equal(max(0.0, c.severity_norm - SEV_BAND)))
        fs.append(Filter.by_property("severity_norm").less_or_equal(min(1.0, c.severity_norm + SEV_BAND)))
    if c.exclude_holdout:
        fs.append(Filter.by_property("split").equal("train"))
        for hid in sorted(c.holdout):
            fs.append(Filter.by_property("event_id").not_equal(hid))
    if c.as_of is not None:
        fs.append(Filter.by_property("event_date").less_than(
            datetime(c.as_of.year, c.as_of.month, c.as_of.day, tzinfo=timezone.utc)))
    if not fs:
        return None
    out = fs[0]
    for f in fs[1:]:
        out = out & f
    return out


def _obj_to_event(obj: Any) -> tuple[dict, np.ndarray | None]:
    p = dict(obj.properties or {})
    for k in ("event_date", "start_date", "end_date"):
        if isinstance(p.get(k), datetime):
            p[k] = p[k].date().isoformat()
    try:
        p["outcomes"] = json.loads(p.get("outcomes_json") or "[]")
    except ValueError:
        p["outcomes"] = []
    v = obj.vector
    if isinstance(v, dict):
        v = v.get("default") if "default" in v else next(iter(v.values()), None)
    return p, (None if v is None else np.asarray(v, dtype=np.float32))


def weaviate_search(req: AnalogReq, embedder: Embedder) -> tuple[list[tuple[float, dict]], list[str], str | None]:
    """Hybrid query per relaxation step; similarity is OUR cosine(qvec, stored vector), hybrid score only ranks.
    Hard filters are applied server-side AND re-checked here. Parity with numpy_search: tests/test_parity_live.py."""
    from weaviate.classes.query import MetadataQuery
    from .schema import HISTORICAL_EVENT_COLLECTION

    coll = wv.get_client().collections.get(HISTORICAL_EVENT_COLLECTION)
    if not coll.aggregate.over_all(total_count=True).total_count:
        raise RuntimeError("Weaviate holds no events (not seeded yet)")    # → numpy fallback, marked degraded
    qvec = embedder.encode_one(req.situation)
    query = (req.situation + " " + (req.region_hint or "")).strip()
    c = Criteria.from_req(req, MIN_SIM)
    events = load_events()                       # same specificity guard as the fallback, from the cached corpus
    if events:
        note = unspecific_warning(specificity(corpus_vectors(events, embedder) @ qvec), c)
        if note:
            return [], [], note
    need = min(MIN_HITS, c.k)
    chosen, relaxed = [], []
    for step in relaxation_steps(c):
        relaxed = step
        res = coll.query.hybrid(query=query, vector=qvec.tolist(), alpha=req.alpha, limit=req.k * 3,
                                filters=_weaviate_filters(c, step), include_vector=True,
                                return_metadata=MetadataQuery(score=True))
        cands = []
        for obj in res.objects:
            e, v = _obj_to_event(obj)
            if v is None or not passes_hard_filters(e, c) or not passes_soft_filters(e, c, step):
                continue
            n = float(np.linalg.norm(v)) or 1.0
            s = float(v @ qvec) / n
            if s >= c.min_sim:
                cands.append((s, e))
        chosen = cands
        if len(chosen) >= need:
            break
    chosen.sort(key=lambda p: (-p[0], p[1].get("event_id", "")))
    return chosen[: c.k], relaxed, None


async def find_analogs(req: AnalogReq, embedder: Embedder | None = None) -> tuple[dict, list[str], SearchMeta]:
    embedder = embedder or Embedder()
    meta = SearchMeta(embedder=embedder.name, n_corpus=len(load_events()))
    warnings: list[str] = []
    chosen, relaxed, note = None, [], None
    chaos_down = bool(req.chaos.get("vector_down"))

    if embedder.is_fallback:
        meta.degraded, meta.reason = True, "embedder_unavailable_hashing_fallback"
        warnings.append("bge-small-en-v1.5 could not be loaded: hashing embedder with raw cosine "
                        f"(threshold {HASH_MIN_SIM}); similarities are NOT comparable to bge scores")
    elif chaos_down:
        meta.degraded, meta.reason = True, "chaos"
        warnings.append("vector_down chaos flag set: numpy fallback")
    elif wv.is_available():
        try:
            chosen, relaxed, note = await asyncio.to_thread(weaviate_search, req, embedder)
            meta.backend = "weaviate"
        except ConnectionError:              # not reachable: breaker is open now, plain fallback
            chosen = None
        except Exception as e:  # noqa: BLE001  any Weaviate problem → fallback, never an error to the caller
            wv.report_failure(e)
            warnings.append(f"weaviate query failed ({type(e).__name__}); numpy fallback")
            chosen = None
    if chosen is None:
        if not meta.reason:
            meta.degraded, meta.reason = True, "weaviate_down_numpy_fallback"
        chosen, relaxed, meta.min_sim, note = await asyncio.to_thread(numpy_search, req, embedder)
        meta.backend = "numpy"
        if meta.n_corpus == 0:
            meta.reason = "no_corpus"
            warnings.append("analog corpus data/historical_events.json is missing (run scripts/build_events.py)")
    value, w2 = assemble_value(chosen, relaxed, req)
    value["backend"] = meta.backend
    value["min_similarity"] = meta.min_sim
    value["n_corpus"] = meta.n_corpus
    value["embedder"] = meta.embedder
    if req.event_type and req.event_type not in EVENT_TYPES:
        warnings.append(f"unknown event_type {req.event_type!r}: no corpus event can match it exactly")
    if note:
        warnings.append(note)
    elif not value["analogs"]:
        warnings.append(f"no analog reached similarity {meta.min_sim}")
    return value, warnings + w2, meta


def evidence_confidence(value: dict, meta: SearchMeta) -> float:
    _, conf = confidence_label(len(value["analogs"]),
                               float(np.mean([a["similarity"] for a in value["analogs"]])) if value["analogs"] else 0.0,
                               len(value.get("filters_relaxed") or []))
    if meta.degraded:
        conf -= 0.1                      # 08 §9
    if meta.reason == "embedder_unavailable_hashing_fallback":
        conf = min(conf, 0.2)
    if meta.reason == "no_corpus":
        conf = 0.05
    return round(max(0.05, conf), 3)
