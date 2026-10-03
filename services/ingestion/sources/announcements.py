"""NSE corporate announcements. Best effort and brittle: needs a browser-like session; may be blocked."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import httpx
from .rss import news_id
from ..timeutil import iso, utcnow

HDRS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept": "application/json,text/plain,*/*", "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements"}


async def fetch(symbol: str, since_hours: int = 72) -> list[dict]:
    ist = timezone(timedelta(hours=5, minutes=30))
    async with httpx.AsyncClient(timeout=15, headers=HDRS, follow_redirects=True) as c:
        await c.get("https://www.nseindia.com")  # cookies
        r = await c.get("https://www.nseindia.com/api/corporate-announcements",
                        params={"index": "equities", "symbol": symbol})
        r.raise_for_status()
        rows = r.json()
    cutoff = utcnow() - timedelta(hours=since_hours)
    out = []
    for a in rows if isinstance(rows, list) else []:
        try:
            pub = datetime.strptime(a["an_dt"], "%d-%b-%Y %H:%M:%S").replace(tzinfo=ist).astimezone(timezone.utc)
        except Exception:
            continue
        if pub < cutoff:
            continue
        url = a.get("attchmntFile") or f"https://www.nseindia.com/companies-listing/corporate-filings-announcements?symbol={symbol}"
        out.append({"news_id": news_id(url + a["an_dt"]), "title": f"{symbol}: {a.get('desc','Announcement')}",
                    "summary": (a.get("attchmntText") or "")[:500], "source": "NSE", "url": url,
                    "published_at": iso(pub), "tickers": [f"{symbol}.NS"], "category": "corporate_announcement"})
    return out
