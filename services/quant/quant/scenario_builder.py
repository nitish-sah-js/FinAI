"""Phase 1 of the hedge review: turn a run's EVIDENCE into percent factor shocks for the existing factor model.

Why: the reviewed design multiplied betas by unitless "risk scores" (strength × confidence × agreement), so P&L and hedge
size had no units. Here every shock is in percent points, read by code from evidence values (never from LLM text):

  analogs   distribution[{asset, horizon, p10, median, p90}] for a factor asset (^NSEI→nifty, CL=F/BZ=F→crude, INR=X→usd_inr)
            → three cases p10 / median / p90 of what that factor did after similar past events
  macro     brent_chg_5d, usd_inr_chg_5d, repo_change_bps → a "recent move persists" shock, used only when no analog covers
            the factor (labelled as an assumption)
  agri      yield_anomaly_pct {q10, q50, q90} → agri_stress shock = −yield anomaly (q10 is the stressful case)
  weather   rain_anomaly_pct → monsoon_rain, only for a non-storm reading (cyclone rain is not a monsoon deficit), clipped

The three cases combine each factor at the SAME quantile (comonotonic: a stated simplification, not a joint model).
"""
from __future__ import annotations

FACTOR_OF_ASSET = {"^NSEI": "nifty", "CL=F": "crude", "BZ=F": "crude", "INR=X": "usd_inr"}
CLIP = {"monsoon_rain": (-40.0, 20.0), "agri_stress": (-20.0, 40.0), "crude": (-40.0, 40.0), "nifty": (-25.0, 25.0),
        "usd_inr": (-10.0, 10.0), "repo_bps": (-100.0, 100.0)}
CASES = ("p10", "median", "p90")
MIN_N_ANALOG = 3      # an analog "distribution" from 1–2 events is not a range: fall back to macro (or skip the factor)


def _clip(f: str, x: float) -> float:
    lo, hi = CLIP.get(f, (-100.0, 100.0))
    return round(max(lo, min(hi, x)), 4)


def build(evidence: list[dict], horizon: str = "5d") -> tuple[dict[str, dict[str, float]], list[dict]]:
    """Return ({case: {factor: shock_pp}}, provenance rows)."""
    per_factor: dict[str, dict] = {}            # factor -> {"p10","median","p90","source","method"}
    by_tool = {}
    for e in evidence:
        by_tool.setdefault(e.get("tool"), e)

    an = by_tool.get("analogs")
    if an:
        for d in (an.get("value") or {}).get("distribution", []) or []:
            f = FACTOR_OF_ASSET.get(d.get("asset"))
            if not f or str(d.get("horizon")) != horizon or f in per_factor or d.get("measure") == "abnormal":
                continue
            if None in (d.get("p10"), d.get("median"), d.get("p90")):
                continue
            if (d.get("n") or 0) < MIN_N_ANALOG:
                continue
            per_factor[f] = {"p10": d["p10"] * 100, "median": d["median"] * 100, "p90": d["p90"] * 100,
                             "source": an["id"], "method": f"analog distribution of {d['asset']} ({d.get('n', '?')} events)"}

    mac = by_tool.get("macro")
    if mac:
        v = mac.get("value") or {}
        for f, key, scale in (("crude", "brent_chg_5d", 100.0), ("usd_inr", "usd_inr_chg_5d", 100.0),
                              ("repo_bps", "repo_change_bps", 1.0)):
            x = v.get(key)
            if f in per_factor or x is None or abs(x) < 1e-9:
                continue
            per_factor[f] = {"p10": x * scale, "median": x * scale, "p90": x * scale, "source": mac["id"],
                             "method": f"recent move {key} persists (assumption)"}

    ag = by_tool.get("agri")
    if ag:
        y = (ag.get("value") or {}).get("yield_anomaly_pct") or {}
        if all(k in y and y[k] is not None for k in ("q10", "q50", "q90")):
            # stress = −yield anomaly; q10 (worst yield) is the most stressful case → map it to the p10 case
            per_factor["agri_stress"] = {"p10": -y["q10"], "median": -y["q50"], "p90": -y["q90"], "source": ag["id"],
                                         "method": "agri model yield anomaly q10/q50/q90 (stress = −yield)"}

    we = by_tool.get("weather")
    if we:
        v = we.get("value") or {}
        if v.get("storm") is None and v.get("rain_anomaly_pct") is not None:
            r = float(v["rain_anomaly_pct"])
            per_factor["monsoon_rain"] = {"p10": r, "median": r, "p90": r, "source": we["id"],
                                          "method": "rain anomaly vs normal (non-storm reading)"}

    cases = {c: {f: _clip(f, row[c]) for f, row in per_factor.items()} for c in CASES}
    prov = [{"factor": f, **{c: _clip(f, row[c]) for c in CASES}, "source_evidence": row["source"], "method": row["method"]}
            for f, row in per_factor.items()]
    return cases, prov
