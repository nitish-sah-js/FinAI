"""Sentiment service on L2:8102 (docs/07). Run from services/sentiment:
    ..\\..\\.venv\\Scripts\\python -m uvicorn sentiment.main:app --host 0.0.0.0 --port 8102
"""
import asyncio
import hashlib
import html
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, time as dtime, timezone

import httpx
from fastapi import Body
from pydantic import BaseModel

from copilot_common.ids import counter_for
from copilot_common.models import Evidence, Portfolio, ToolResult
from copilot_common.service_base import create_service_app, degraded, mock_or, now_utc
from copilot_common.settings import get_settings

from .hinglish import is_hinglish
from .model import score_async
from .second_opinion import PROMPT_VERSION, ask_gemma, merge
from .startup import startup
from .ticker_map import search_tickers
from .weighting import compute_weights, portfolio_sentiment

DEFAULT_MODEL = "kdave/FineTuned_Finbert"
_state = {"finbert": "not loaded", "device": None}


def finbert_name() -> str:
    return os.getenv("SENTIMENT_MODEL", DEFAULT_MODEL)


@asynccontextmanager
async def lifespan(app):
    if not get_settings().MOCK:          # MOCK=1: FinBERT is not loaded at all, startup < 1 s (07 §6)
        await startup()
        from .model import get_scorer
        try:
            sc = await asyncio.to_thread(get_scorer)   # load now, not on the first request
            _state.update(finbert="ok", device=getattr(sc, "device", None))
        except Exception as e:  # noqa: BLE001  report it on /health instead of refusing to start
            _state.update(finbert=f"load failed: {type(e).__name__}: {str(e)[:120]}")
    yield


async def deps_check() -> dict[str, str]:
    s = get_settings()
    if s.MOCK:
        return {"finbert": "ok (mock)"}
    from . import ticker_map
    deps = {"finbert": _state["finbert"] if _state["finbert"] != "ok" else "ok",
            "ticker_index": "ok" if ticker_map._index is not None and ticker_map._index.ready else "not built"}
    try:
        async with httpx.AsyncClient(timeout=1.5) as c:
            r = await c.get(f"{s.OLLAMA_L2.rstrip('/')}/api/tags")
            names = [m.get("name", "") for m in r.json().get("models", [])]
        want = s.OLLAMA_MODEL_L2
        deps["second_opinion"] = ("ok" if any(n == want or n.startswith(want + ":") for n in names)
                                  else f"{want} not pulled on L2 (falls back to {s.OLLAMA_MODEL_L1} on L1)")
    except (httpx.HTTPError, ValueError) as e:
        deps["second_opinion"] = f"Ollama L2 down ({type(e).__name__}); FinBERT labels are kept"
    return deps


app = create_service_app("sentiment", deps_check=deps_check, models=[finbert_name()], lifespan=lifespan)


# ── request models ────────────────────────────────────────────────────────────
class NewsIn(BaseModel):
    news_id:      str
    title:        str
    summary:      str        = ""
    source:       str        = ""
    published_at: datetime
    tickers:      list[str]  = []


class ScoreReq(BaseModel):
    items:          list[NewsIn]
    portfolio:      Portfolio | None = None      # the shared contract model (01 §5)
    second_opinion: bool             = True
    as_of:          date | None      = None
    run_id:         str | None       = None


class EvalReq(BaseModel):
    n:     int        = 200
    model: str | None = None


_TAGS = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    """Strip HTML, unescape entities, collapse whitespace (07 §3.1)."""
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub(" ", text or ""))).strip()


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── /sentiment/score ──────────────────────────────────────────────────────────
@app.post("/sentiment/score")
async def score(req: ScoreReq):
    return await mock_or("sentiment", "/sentiment/score", lambda: _score(req))


async def _score(req: ScoreReq) -> dict:
    t0 = time.perf_counter()
    warnings: list[str] = []

    # 1. normalise, time-machine filter, dedupe by title hash
    cutoff = datetime.combine(req.as_of, dtime(23, 59, 59), tzinfo=timezone.utc) if req.as_of else None
    seen: set[str] = set()
    unique_items: list[NewsIn] = []
    late = 0
    for item in req.items:
        item = item.model_copy(update={"title": _clean(item.title), "summary": _clean(item.summary)})
        if cutoff and _utc(item.published_at) > cutoff:
            late += 1
            continue
        h = hashlib.md5(item.title.lower().encode()).hexdigest()
        if item.title and h not in seen:
            seen.add(h)
            unique_items.append(item)
    if late:
        warnings.append(f"{late} headline(s) published after as_of were ignored")
    if not unique_items:
        return ToolResult(evidence=[], warnings=warnings + ["no items after dedup"]).model_dump(mode="json")

    # 2. FinBERT batch
    texts = [f"{it.title}. {it.summary[:300]}" for it in unique_items]
    fb_results = await score_async(texts)

    # 3. ticker linking + Hinglish + which items need a second opinion
    scored_items, so_tasks = [], []
    for item, fb in zip(unique_items, fb_results):
        hinglish = is_hinglish(item.title)
        needs_so = req.second_opinion and (fb["confidence"] < 0.6 or hinglish)
        ticker_matches = await search_tickers(item.title + " " + item.summary, item.tickers)
        scored_items.append({
            **fb,
            "news_id":        item.news_id,
            "published_at":   _utc(item.published_at),
            "hinglish":       hinglish,
            "ticker_matches": ticker_matches,
            "tickers":        [t for t, _ in ticker_matches[:3]],
            "relevance":      ticker_matches[0][1] if ticker_matches else 0.2,
            "second_opinion": None,
            "model":          finbert_name(),
            "_needs_so":      needs_so,
        })
        so_tasks.append(ask_gemma(item.title, req.run_id) if needs_so else None)

    # 4. second opinions in parallel; gemma down → FinBERT label kept + warning (07 §7)
    so_results = await asyncio.gather(*[t if t else asyncio.sleep(0, result=None) for t in so_tasks])
    failed = 0
    for entry, so in zip(scored_items, so_results):
        if entry["_needs_so"] and so is None:
            failed += 1
        if so:
            entry.update(merge(entry, so))
            if "_warning" in entry:
                warnings.append(f"label disagreement on {entry['news_id']} → {entry['label']} "
                                f"(LLM conf {so.get('confidence', 0):.2f} < 0.7)")
    if failed:
        warnings.append(f"second opinion unavailable for {failed} headline(s); FinBERT label kept")

    # 5. weighting
    compute_weights(scored_items, req.portfolio, warnings)
    port_score, by_ticker = portfolio_sentiment(scored_items, req.portfolio, warnings)

    # 6. evidence confidence (07 §3.7)
    n = len(scored_items)
    mean_conf = sum(e["confidence"] for e in scored_items) / n
    evidence_conf = mean_conf * min(1.0, n / 10)
    if n < 3:
        warnings.append("few headlines")
        evidence_conf = min(evidence_conf, 0.3)

    clean_items = [{
        "news_id":        e["news_id"],
        "label":          e["label"],
        "score":          e["score"],
        "confidence":     e["confidence"],
        "model":          e["model"],
        "second_opinion": e.get("second_opinion"),
        "weight":         e.get("weight", 0),
        "relevance":      e["relevance"],
        "hinglish":       e["hinglish"],
        "tickers":        e["tickers"],
    } for e in scored_items]

    so_models = sorted({e["second_opinion"]["model"] for e in scored_items if e.get("second_opinion")})
    n_hing = sum(1 for e in scored_items if e["hinglish"])
    n_so = sum(1 for e in scored_items if e.get("second_opinion"))
    now = now_utc()
    as_of = max(e["published_at"] for e in scored_items)
    ev = Evidence(
        id=counter_for(req.run_id).next("sentiment") if req.run_id else "ev_sentiment_001",
        run_id=req.run_id, tool="sentiment",
        value={"items": clean_items, "portfolio_sentiment": float(port_score), "by_ticker": by_ticker},
        summary=f"Portfolio news tone {port_score:+.2f} ({n} headlines; {n_hing} Hinglish; {n_so} second opinions)",
        source=f"FinBERT ({finbert_name()})" + (f" + {', '.join(so_models)} second opinion" if so_models else ""),
        as_of=as_of, timestamp=now, freshness_s=max(0, int((now - as_of).total_seconds())),
        confidence=round(evidence_conf, 3), degraded=False, latency_ms=int((time.perf_counter() - t0) * 1000),
        model_version=f"{finbert_name().split('/')[-1]}+{'+'.join(so_models) or 'none'}_{PROMPT_VERSION}",
    )
    return ToolResult(evidence=[ev], warnings=warnings).model_dump(mode="json")


# ── /sentiment/eval ───────────────────────────────────────────────────────────
@app.post("/sentiment/eval")
async def evaluate_endpoint(req: EvalReq = Body(default_factory=EvalReq)):
    """Offline accuracy report (07 §5 step 8): accuracy, macro-F1, confusion, per-class, majority baseline."""
    now = now_utc()
    if get_settings().MOCK:
        ev = degraded("sentiment", "ev_sentiment_001", "mock", value={"eval": None}, source="sentiment/eval.py")
        return ToolResult(evidence=[ev], warnings=["eval skipped in MOCK mode"]).model_dump(mode="json")
    from .eval import evaluate, load_hf
    rows = await asyncio.to_thread(load_hf, req.n)
    rep = await asyncio.to_thread(evaluate, rows, req.model)
    best = rep["orders"][rep["best_order"]]
    ev = Evidence(id="ev_sentiment_001", tool="sentiment", value={"eval": rep},
                  summary=f"{rep['model']}: accuracy {best['accuracy']}, macro-F1 {best['macro_f1']} on {rep['n']} rows "
                          f"(baseline {rep['baseline_majority']['macro_f1']})",
                  source="sentiment/eval.py on kdave/Indian_Financial_News", as_of=now, timestamp=now,
                  confidence=None, model_version=rep["model"])
    return ToolResult(evidence=[ev]).model_dump(mode="json")
