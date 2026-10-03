"""Alert headline (P11 via the gateway role "alert" → qwen3:1.7b on L3) with a code post-check and template fallback.
The template is instant, so the LLM can never delay an alert beyond writer.timeout_s."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from .detectors import Candidate

KIND_LABEL = {"price_z": "unusual price move", "volume_z": "unusual volume", "news_burst": "news burst",
              "sentiment_shift": "sentiment shift", "weather_threshold": "severe weather", "agri_stress": "crop stress"}
_P11 = Path(__file__).resolve().parents[2] / "orchestrator" / "prompts" / "P11.txt"
_P11_FALLBACK = ("Write ONE sentence under 25 words: what happened, which holding, why it matters, and the confidence.\n"
                 "FACTS: {alert_facts_json}")
_NUM = re.compile(r"-?\d+(?:[.,]\d+)*")


def conf_label(c: float) -> str:
    return "high" if c >= 0.75 else "medium" if c >= 0.5 else "low"


def key_fact(c: Candidate) -> str:
    f = c.facts
    if c.kind == "price_z":
        return f"{f['r_5m_pct']:+.2f}% in 5 min, z {f['z']:+.1f}"
    if c.kind == "volume_z":
        return f"30-min volume z {f['z']:.1f}"
    if c.kind == "news_burst":
        return f"{f['k']} headlines in the last hour vs about {f['lambda']:g} normally"
    if c.kind == "sentiment_shift":
        return f"sentiment {f['mean_24h']:+.2f} → {f['mean_6h']:+.2f} over 6 h"
    if c.kind == "weather_threshold":
        return f"{', '.join(f['alerts'])} in {f['region']}"
    if c.kind == "agri_stress":
        return f"{f['region']} now {f['stress_class']}"
    return c.kind


def subject(c: Candidate) -> str:
    return c.tickers[0] if len(c.tickers) == 1 else ", ".join(t.removesuffix(".NS") for t in c.tickers[:3])


def template(c: Candidate, weight: float) -> str:
    return (f"{subject(c)}: {KIND_LABEL.get(c.kind, c.kind)} ({key_fact(c)}); {weight:.0%} of portfolio; "
            f"confidence {conf_label(c.confidence)}.")


def facts_for_llm(c: Candidate, weight: float) -> dict:
    return {"kind": KIND_LABEL.get(c.kind, c.kind), "tickers": c.tickers, "portfolio_weight_pct": round(weight * 100),
            "confidence": conf_label(c.confidence), **c.facts}


def _numbers(x) -> set[float]:
    out: set[float] = set()
    for m in _NUM.findall(json.dumps(x)):
        try:
            out.add(round(abs(float(m.replace(",", ""))), 2))
        except ValueError:
            pass
    return out


def post_check(text: str, facts: dict, max_words: int = 25) -> bool:
    """One line, < max_words words, and every number in the headline appears in the facts (validator idea from 04)."""
    text = (text or "").strip()
    if not text or "\n" in text or len(text.split()) >= max_words:
        return False
    allowed = _numbers(facts)
    for m in _NUM.findall(text.replace(".NS", "")):
        try:
            v = round(abs(float(m.replace(",", ""))), 2)
        except ValueError:
            return False
        if v not in allowed and not any(abs(v - a) <= max(0.051, 0.005 * a) for a in allowed):
            return False
    return True


async def write_headline(c: Candidate, weight: float, timeout_s: float = 1.5, max_words: int = 25) -> tuple[str, str]:
    """Return (headline, source) where source is "llm" or "template"."""
    facts = facts_for_llm(c, weight)
    try:
        from copilot_llm import llm
        prompt = (_P11.read_text(encoding="utf-8") if _P11.exists() else _P11_FALLBACK)
        prompt = prompt.replace("{alert_facts_json}", json.dumps(facts, ensure_ascii=False))
        res = await asyncio.wait_for(llm.chat("alert", [{"role": "user", "content": prompt}], max_tokens=60,
                                              timeout_s=timeout_s), timeout_s + 0.2)
        text = (res.text or "").strip().strip('"')
        if res.ok and post_check(text, facts, max_words):
            return text, "llm"
    except Exception:  # noqa: BLE001 - LLM down/slow/raising → template, never delay the alert
        pass
    return template(c, weight), "template"


def reason_from_facts(c: Candidate) -> str:
    f = c.facts
    if c.kind == "weather_threshold":
        links = ", ".join(f"{t.removesuffix('.NS')} {s}" for t, s in f.get("links", {}).items())
        extra = f"; rain {f['rain_anomaly_pct']:+.0f}% vs normal" if f.get("rain_anomaly_pct") is not None else ""
        return f"{f['region']} alerts [{', '.join(f['alerts'])}]{extra}; link strength {links}"
    if c.kind == "agri_stress":
        return f"{f['region']}: {f.get('prev_class') or 'unknown'} → {f['stress_class']}"
    if c.kind == "news_burst":
        return f"k={f['k']}, lambda={f['lambda']:g}, z={f['z']:.1f}"
    if c.kind == "price_z":
        return f"5-min return {f['r_5m_pct']:+.2f}%, z={f['z']:+.1f} ({f['history_days']} days of history)"
    if c.kind == "volume_z":
        return f"30-min volume {f['volume_30m']:,} vs normal {f['normal_30m']:,} (z={f['z']:.1f})"
    if c.kind == "sentiment_shift":
        return f"mean sentiment {f['mean_24h']:+.2f} (prior 24 h) → {f['mean_6h']:+.2f} (last 6 h, n={f['n_6h']})"
    return json.dumps(f)


def suggested_query(c: Candidate) -> str:
    f, t = c.facts, c.tickers[0]
    if c.kind == "weather_threshold":
        return f"How does the {', '.join(f['alerts'])} in {f['region']} affect my portfolio over 5 days?"
    if c.kind == "agri_stress":
        return f"Crop stress in {f['region']}: what does it mean for my holdings?"
    if c.kind == "price_z":
        return f"Why did {t} {'spike' if f['z'] > 0 else 'drop'} and should I hedge?"
    if c.kind == "news_burst":
        return f"What is the news on {t.removesuffix('.NS')} and does it matter?"
    if c.kind == "sentiment_shift":
        return f"Why has sentiment on {t.removesuffix('.NS')} shifted and does it matter?"
    return f"What is happening with {t}?"
