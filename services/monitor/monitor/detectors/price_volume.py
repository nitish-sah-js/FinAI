from __future__ import annotations

import math

from . import Candidate

EPS = 1e-9          # 0.009/0.003 = 2.9999…: compare with a tolerance so the documented boundary triggers


def detect_price_z(prices: list[dict], thresholds: dict) -> list[Candidate]:
    """prices: [{ticker, r_5m, sigma_5m, history_days}] → |z| ≥ price_z."""
    out = []
    thresh = thresholds.get("price_z", 3.0)
    for p in prices:
        sigma, r = p.get("sigma_5m"), p.get("r_5m")
        if not sigma or r is None:
            continue
        z = r / sigma
        if abs(z) >= thresh - EPS:
            out.append(Candidate(
                kind="price_z", tickers=[p["ticker"]], key=p["ticker"],
                severity=round(min(abs(z) / 6.0, 1.0), 4), relevance=1.0,
                confidence=0.8 if p.get("history_days", 0) >= 20 else 0.5,
                facts={"z": round(z, 2), "r_5m_pct": round(r * 100, 2), "history_days": p.get("history_days", 0)},
                evidence_ids=p.get("evidence_ids", [])))
    return out


def detect_volume_z(volumes: list[dict], thresholds: dict) -> list[Candidate]:
    """volumes: [{ticker, v_30m, mean_log_v, std_log_v}] → z(log V_30m vs same time of day) ≥ volume_z."""
    out = []
    thresh = thresholds.get("volume_z", 3.0)
    for v in volumes:
        if not v.get("v_30m") or v["v_30m"] <= 0 or not v.get("std_log_v"):
            continue
        z = (math.log(v["v_30m"]) - v["mean_log_v"]) / v["std_log_v"]
        if z >= thresh - EPS:
            out.append(Candidate(
                kind="volume_z", tickers=[v["ticker"]], key=v["ticker"],
                severity=round(min(z / 6.0, 1.0), 4), relevance=1.0, confidence=0.7,
                facts={"z": round(z, 2), "volume_30m": int(v["v_30m"]),
                       "normal_30m": int(round(math.exp(v["mean_log_v"])))}))
    return out
