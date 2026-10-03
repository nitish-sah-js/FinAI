from __future__ import annotations
import asyncio, time
from datetime import datetime, timezone
import httpx
from .rss import news_id, tag_tickers
from ..timeutil import iso

URL = "https://api.gdeltproject.org/api/v2/doc/doc"
MIN_GAP_S = 8.0          # GDELT says "one every 5 seconds" but 429s at ~7 s spacing in practice (tested 2026-10-03)
COOLDOWN_S = 60          # after a 429, skip GDELT (fail fast, RSS still answers) instead of stalling callers
_lock = asyncio.Lock()
_last = 0.0
_cool_until = 0.0


class GdeltRateLimited(RuntimeError):
    pass


async def _throttle() -> None:
    """Process-wide spacing shared by the worker and the /news endpoint."""
    global _last
    if time.monotonic() < _cool_until:
        raise GdeltRateLimited(f"GDELT 429 cooldown ({_cool_until - time.monotonic():.0f}s left)")
    async with _lock:
        wait = _last + MIN_GAP_S - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _last = time.monotonic()


async def search(query: str, tickers_cfg: dict, country: str = "IN", timespan: str = "48h",
                 start: datetime | None = None, end: datetime | None = None, maxrecords: int = 50) -> list[dict]:
    """GDELT DOC 2.0. Rate limit is roughly 1 request / 5 s, so rely on the cache."""
    params = {"query": f"{query} sourcecountry:{country}", "mode": "artlist",
              "format": "json", "maxrecords": maxrecords}
    if start and end:  # as_of / time machine
        params["startdatetime"] = start.strftime("%Y%m%d%H%M%S")
        params["enddatetime"] = end.strftime("%Y%m%d%H%M%S")
    else:
        params["timespan"] = timespan
    global _cool_until
    await _throttle()
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(URL, params=params)
    if r.status_code == 429:
        _cool_until = time.monotonic() + COOLDOWN_S
        raise GdeltRateLimited("GDELT 429 Too Many Requests")
    r.raise_for_status()
    try:
        arts = r.json().get("articles", [])
    except Exception:  # GDELT returns plain text when there are no results
        return []
    out = []
    for a in arts:
        try:
            pub = datetime.strptime(a["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        title = a.get("title", "")
        out.append({"news_id": news_id(a["url"]), "title": title, "summary": "",
                    "source": f"GDELT:{a.get('domain','')}", "url": a["url"], "published_at": iso(pub),
                    "tickers": tag_tickers(title, tickers_cfg), "category": "news"})
    return out
