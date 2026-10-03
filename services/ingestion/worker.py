"""Background loops: news -> sentiment -> vectordb -> ring buffer; price ticks; dependency pings."""
from __future__ import annotations
import asyncio, json, logging, os, sqlite3
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from copilot_common.models import NewsScoreItem, SentimentScoreRequest
from copilot_common.settings import get_settings
from . import store
from .sources import gdelt, prices_yf, rss
from .timeutil import iso, parse_dt, utcnow
from .util import CFG, is_mock, is_replay, load_json

log = logging.getLogger("ingestion.worker")
RING: deque = deque(maxlen=2000)     # news items
TICKS: deque = deque(maxlen=2000)    # price ticks
RETRY: deque = deque(maxlen=500)     # items not yet delivered downstream
DEPS = {"sentiment": "unknown", "vectordb": "unknown", "open_meteo": "unknown"}
_last_tick: dict[str, str] = {}


class AwaitableDict(dict):
    """Works whether service_base calls deps_check() sync or `await deps_check()`."""
    def __await__(self):
        async def _c():
            return dict(self)
        return _c().__await__()


def check_deps() -> dict:
    return AwaitableDict(DEPS)


def _db() -> sqlite3.Connection:
    p = store.data_dir() / "ingest_seen.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p)
    c.execute("create table if not exists seen(news_id text primary key)")
    return c


def _filter_new(items: list[dict]) -> list[dict]:
    c, new = _db(), []
    for it in items:
        if c.execute("select 1 from seen where news_id=?", (it["news_id"],)).fetchone():
            continue
        c.execute("insert into seen values(?)", (it["news_id"],))
        new.append(it)
    c.commit(); c.close()
    return new


def sentiment_payload(items: list[dict]) -> dict:
    """Request body for sentiment /sentiment/score, built with the shared contract model.
    published_at is the feed date when parseable, else now (the field is required for time decay)."""
    out = []
    for i in items:
        try:
            pub = parse_dt(i["published_at"]) if i.get("published_at") else None
        except (TypeError, ValueError):
            pub = None
        out.append(NewsScoreItem(news_id=i["news_id"], title=i.get("title") or "", summary=i.get("summary") or "",
                                 source=i.get("source") or "", tickers=i.get("tickers") or [],
                                 **({"published_at": pub} if pub else {})))
    return SentimentScoreRequest(items=out).model_dump(mode="json")


async def push_downstream(items: list[dict]) -> None:
    if not items:
        return
    s = get_settings()
    sent_url, vec_url = s.SENTIMENT_URL.rstrip("/"), s.VECTOR_URL.rstrip("/")
    ok_vec = True
    async with httpx.AsyncClient(timeout=15) as c:
        try:
            r = await c.post(f"{sent_url}/sentiment/score", json=sentiment_payload(items))
            r.raise_for_status()
            by_id = {x["news_id"]: x for x in r.json()["evidence"][0]["value"]["items"]}
            for i in items:
                s = by_id.get(i["news_id"], {})
                i["sentiment_label"], i["sentiment_score"] = s.get("label"), s.get("score")
            DEPS["sentiment"] = "ok"
        except Exception as e:
            DEPS["sentiment"] = "down"; log.warning("sentiment down: %s", e)
        try:
            r = await c.post(f"{vec_url}/news/index", json={"items": [
                {k: i.get(k) for k in ("news_id", "title", "summary", "source", "published_at", "tickers",
                                       "sentiment_label", "sentiment_score", "ingested_at")} for i in items]})
            r.raise_for_status()
            DEPS["vectordb"] = "ok"
        except Exception as e:
            ok_vec = False; DEPS["vectordb"] = "down"; log.warning("vectordb down: %s", e)
    if DEPS["sentiment"] == "down" or not ok_vec:
        RETRY.extend(items)       # never block ingestion; try again next cycle


async def _ingest(items: list[dict]) -> None:
    now = iso(utcnow())
    for i in items:
        i.setdefault("ingested_at", now)
    batch = list(RETRY) + items
    RETRY.clear()
    RING.extend(items)
    await push_downstream(batch)


async def news_loop() -> None:
    poll = int(os.getenv("NEWS_POLL_S", "120"))
    cfg, feeds = load_json("tickers.json"), load_json("rss_feeds.json")
    queries = [q.strip() for q in os.getenv("DEMO_QUERIES", "cyclone odisha,hurricane gulf").split(",") if q.strip()]
    while True:
        try:
            items = await rss.fetch_feeds(feeds, cfg)
            for q in queries:
                try:
                    items += await gdelt.search(q, cfg, country="US" if "hurricane" in q else "IN")
                except Exception as e:
                    log.warning("gdelt %s: %s", q, e)
            await _ingest(_filter_new(rss.dedupe(items)))
        except Exception:
            log.exception("news cycle failed")
        await asyncio.sleep(poll)


async def replay_loop() -> None:
    """REPLAY=1 / CACHE_MODE=replay / MOCK=1: replay data/cache/ingestion/news_replay.jsonl at x10 speed."""
    p = store.data_dir() / "cache/ingestion/news_replay.jsonl"
    while True:
        if not p.exists():
            log.warning("no %s; replay idle", p); await asyncio.sleep(30); continue
        prev = None
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            it = json.loads(line)
            t = parse_dt(it["published_at"])
            if prev is not None:
                await asyncio.sleep(min(max((t - prev).total_seconds(), 0) / 10, 5))
            prev = t
            it["ingested_at"] = iso(utcnow())
            await _ingest([it])
        await asyncio.sleep(10)


def market_open() -> bool:
    ist = utcnow().astimezone(timezone(timedelta(hours=5, minutes=30)))
    return ist.weekday() < 5 and (9, 15) <= (ist.hour, ist.minute) <= (15, 30)


async def price_loop() -> None:
    universe = list(load_json("tickers.json"))
    while True:
        try:
            for t, df in (await prices_yf.fetch_prices(universe, "5d", "5m")).items():
                store.save_prices(t, "5m", df)
                for ts, r in df.tail(3).iterrows():
                    stamp = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
                    if _last_tick.get(t, "") < stamp:
                        _last_tick[t] = stamp
                        TICKS.append({"ticker": t, "ts": stamp, "close": float(r.close), "volume": int(r.volume)})
        except Exception:
            log.exception("price cycle failed")
        await asyncio.sleep(int(os.getenv("PRICE_POLL_S", "60")) if market_open() else 900)


async def deps_loop() -> None:
    while True:
        async with httpx.AsyncClient(timeout=5) as c:
            for name, url in (("sentiment", get_settings().SENTIMENT_URL.rstrip("/")),
                              ("vectordb", get_settings().VECTOR_URL.rstrip("/"))):
                try:
                    (await c.get(f"{url}/health")).raise_for_status(); DEPS[name] = "ok"
                except Exception:
                    DEPS[name] = "down"
            if is_replay() or is_mock():
                DEPS["open_meteo"] = "skipped"
            else:
                try:
                    (await c.get("https://api.open-meteo.com/v1/forecast?latitude=0&longitude=0&daily=precipitation_sum&forecast_days=1")).raise_for_status()
                    DEPS["open_meteo"] = "ok"
                except Exception:
                    DEPS["open_meteo"] = "down"
        await asyncio.sleep(30)


def feed_since(ts: str | None) -> dict:
    """No ts -> the whole ring buffer. Ticks are under "ticks" (what 11 Monitor reads) and "prices" (alias)."""
    t = parse_dt(ts) if ts else datetime.min.replace(tzinfo=timezone.utc)
    ticks = [k for k in TICKS if parse_dt(k["ts"]) > t]
    return {"now": iso(utcnow()),
            "news": [i for i in RING if parse_dt(i["ingested_at"]) > t],
            "ticks": ticks, "prices": ticks}


def start() -> list[asyncio.Task]:
    get_settings()  # loads .env into os.environ before the WORKER/REPLAY/NEWS_POLL_S knobs are read
    if os.getenv("WORKER", "1") == "0":
        return []
    replay = os.getenv("REPLAY") == "1" or is_replay() or is_mock()
    loops = [replay_loop, deps_loop] if replay else [news_loop, price_loop, deps_loop]
    return [asyncio.create_task(f()) for f in loops]


def stop(tasks: list[asyncio.Task]) -> None:
    for t in tasks:
        t.cancel()
