from __future__ import annotations
import asyncio, hashlib, logging, re
from calendar import timegm
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit
import feedparser, httpx
from rapidfuzz import fuzz
from ..timeutil import iso, utcnow

log = logging.getLogger("ingestion.rss")
UA = {"User-Agent": "Mozilla/5.0 (compatible; CopilotIngestion/0.1)"}


def normalize_url(u: str) -> str:
    p = urlsplit(u.strip())
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), "", ""))


def news_id(url: str) -> str:
    return hashlib.sha1(normalize_url(url).encode()).hexdigest()[:12]


def tag_tickers(text: str, tickers_cfg: dict) -> list[str]:
    found = []
    for t, meta in tickers_cfg.items():
        for alias in meta.get("aliases", []):
            flags = 0 if (alias.isupper() and len(alias) <= 5) else re.IGNORECASE
            if re.search(rf"\b{re.escape(alias)}\b", text, flags):
                found.append(t)
                break
    return found


def dedupe(items: list[dict]) -> list[dict]:
    seen_ids, out = set(), []
    for it in items:
        if it["news_id"] in seen_ids:
            continue
        if any(fuzz.token_set_ratio(it["title"], o["title"]) > 90 for o in out):
            continue
        seen_ids.add(it["news_id"])
        out.append(it)
    return out


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


async def _one(client: httpx.AsyncClient, feed: dict, tickers_cfg: dict) -> list[dict]:
    try:
        r = await client.get(feed["url"], headers=UA, follow_redirects=True)
        r.raise_for_status()
    except Exception as e:
        log.warning("feed failed %s: %s", feed["source"], e)
        return []
    parsed = feedparser.parse(r.content)
    items = []
    for e in parsed.entries:
        url = e.get("link")
        if not url:
            continue
        tp = e.get("published_parsed") or e.get("updated_parsed")
        pub = datetime.fromtimestamp(timegm(tp), timezone.utc) if tp else utcnow()
        title, summary = _clean(e.get("title", "")), _clean(e.get("summary", ""))[:500]
        items.append({"news_id": news_id(url), "title": title, "summary": summary,
                      "source": feed["source"], "url": url, "published_at": iso(pub),
                      "tickers": tag_tickers(f"{title} {summary}", tickers_cfg), "category": "news"})
    return items


async def fetch_feeds(feeds: list[dict], tickers_cfg: dict) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        res = await asyncio.gather(*[_one(client, f, tickers_cfg) for f in feeds])
    items = [i for sub in res for i in sub]
    items.sort(key=lambda i: i["published_at"], reverse=True)
    return dedupe(items)
