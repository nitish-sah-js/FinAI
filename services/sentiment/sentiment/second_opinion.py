"""LLM second opinion for low-confidence or Hinglish headlines (07 §3.4), prompt P3 (05).

Goes through the shared gateway (copilot_llm, role "sentiment2"): gemma3:4b on L2, falling back to qwen3 on L1,
with the response cache, quota/usage tracking and MOCK fixtures for free. Calling Ollama directly with
extra_body={"think": False} fails on gemma3 (Ollama rejects thinking options for models without thinking).
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from copilot_llm import llm

_sem = asyncio.Semaphore(int(os.getenv("OLLAMA_CONCURRENCY", "8")))
P3 = (Path(__file__).parent / "prompts" / "P3.txt").read_text(encoding="utf-8").strip()
TIMEOUT_S = 10.0
PROMPT_VERSION = "P3v1"


class SecondOpinion(BaseModel):
    sentiment: Literal["positive", "neutral", "negative"]
    confidence: float = Field(ge=0, le=1)
    affected_tickers: list[str] = Field(default_factory=list, max_length=5)
    materiality: Literal["low", "medium", "high"] = "low"
    horizon: Literal["intraday", "days", "weeks"] = "days"


async def ask_gemma(headline: str, run_id: str | None = None) -> dict | None:
    """Return the P3 JSON plus "model", or None when every model failed (the caller keeps FinBERT's label)."""
    async with _sem:
        res = await llm.chat("sentiment2", [{"role": "user", "content": P3.replace("{headline}", headline)}],
                             schema=SecondOpinion, max_tokens=150, timeout_s=TIMEOUT_S, run_id=run_id)
    if not res.ok or res.parsed is None:
        return None
    out = res.parsed.model_dump()
    out["model"] = res.model or res.provider
    return out


def merge(finbert: dict, llm: dict | None) -> dict:
    if llm is None:
        return finbert

    fb_label  = finbert["label"]
    llm_label = llm["sentiment"]
    llm_conf  = float(llm.get("confidence", 0))

    if fb_label == llm_label:
        return {**finbert,
                "confidence":    max(finbert["confidence"], llm_conf),
                "second_opinion": llm}

    # When the label changes, the score must follow it: portfolio_sentiment aggregates `score`, and keeping FinBERT's
    # +0.08 on a headline now labelled "negative" (seen on "Sensex aaj 500 ank gir gaya") pushed the portfolio the wrong way.
    if llm_conf >= 0.7:
        return {**finbert,
                "label":          llm_label,
                "score":          round(_SIGN[llm_label] * llm_conf, 4),
                "confidence":     llm_conf,
                "second_opinion": llm}

    return {**finbert,
            "label":          "neutral",
            "score":          0.0,
            "confidence":     0.4,
            "second_opinion": llm,
            "_warning": f"label disagreement → neutral (LLM conf {llm_conf:.2f} < 0.7)"}


_SIGN = {"positive": 1.0, "neutral": 0.0, "negative": -1.0}
