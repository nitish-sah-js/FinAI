"""python -m services.ingestion.prewarm   (run the night before the demo; needs internet)"""
from __future__ import annotations
import asyncio, os, time
from types import SimpleNamespace as NS

os.environ.setdefault("CACHE_MODE", "record")
from copilot_common.ids import EvidenceCounter, new_run_id
from . import handlers as H
from .util import load_json


async def main() -> None:
    rid = new_run_id(); c = EvidenceCounter(rid); rows = []

    async def run(label, coro):
        t = time.time()
        try:
            res = await coro
            ev = res.evidence[0]
            rows.append((label, "DEGRADED" if ev.degraded else "ok", f"{time.time()-t:.1f}s", (ev.summary or "")[:60]))
        except Exception as e:
            rows.append((label, "FAIL", f"{time.time()-t:.1f}s", f"{type(e).__name__}: {e}"[:60]))

    # A region's first run pulls 25 y of ERA5 climatology, which Open-Meteo weights as ~100 calls against its
    # ~600/min limit: pace the regions and retry any 429s after a cooldown (climatology is then cached forever).
    pending = load_json("regions.json")
    for attempt in range(3):
        failed = []
        for r in pending:
            await run(f"weather {r['region_id']}", H.weather_handler(
                NS(region_id=r["region_id"], lat=None, lon=None, horizon_days=5, as_of=None), rid, c, time.time(), set()))
            if rows[-1][1] == "FAIL" and "429" in rows[-1][3]:
                failed.append(r)
                rows.pop()
            await asyncio.sleep(12)
        pending = failed
        if not pending:
            break
        print(f"Open-Meteo rate limit: retrying {len(pending)} region(s) in 65 s ...")
        await asyncio.sleep(65)
    for r in pending:
        rows.append((f"weather {r['region_id']}", "FAIL", "-", "Open-Meteo 429 after 3 attempts; rerun prewarm later"))
    await run("prices (2y daily)", H.prices_handler(
        NS(tickers=list(load_json("tickers.json")), period="2y", interval="1d", start=None, end=None, as_of=None),
        rid, c, time.time(), set()))
    await run("macro", H.macro_handler(NS(as_of=None), rid, c, time.time(), set()))
    for q in ("cyclone odisha", "hurricane gulf", None):
        await run(f"news {q}", H.news_handler(NS(query=q, tickers=[], since_hours=168, limit=100, as_of=None), rid, c, time.time(), set()))
        await asyncio.sleep(10)  # GDELT 429s below ~8 s spacing
    print(f"\n{'task':32}{'status':10}{'time':8}summary")
    for r in rows:
        print(f"{r[0]:32}{r[1]:10}{r[2]:8}{r[3]}")


if __name__ == "__main__":
    asyncio.run(main())
