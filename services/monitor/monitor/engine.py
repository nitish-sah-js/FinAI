"""The monitor pipeline (11 §3–7): detectors → impact/tier → cooldown/merge → writer → Alert → store → deliver,
plus the background tasks that feed it. Never calls the orchestrator graph."""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import httpx
import pandas as pd

from copilot_common import reachability
from copilot_common.ids import new_alert_id
from copilot_common.models import Alert
from copilot_common.service_base import FIXTURES_DIR, now_utc
from copilot_common.settings import get_settings

from . import portfolio as pf
from . import sources, stats, store
from .config import get_config
from .dedupe import CooldownManager
from .delivery import email, telegram
from .delivery.ws_hub import hub
from .detectors import (Candidate, detect_agri_stress, detect_news_burst, detect_price_z, detect_sentiment_shift,
                        detect_volume_z, detect_weather_threshold, weather_kinds)
from .scoring import apply_watchlist, impact_score, tier
from .writer import reason_from_facts, suggested_query, write_headline

log = logging.getLogger("monitor.engine")
RETRY_DOWN_S = 60    # poll again this soon when an upstream service was down (instead of the full interval)
FEED_REPLAY = FIXTURES_DIR / "monitor" / "feed_replay.jsonl"
EXAMPLES = FIXTURES_DIR / "monitor" / "example_alerts.json"


def deeplink(query: str, alert_id: str) -> str:
    return f"http://{get_settings().L3_HOST}:3000/run/new?{urlencode({'q': query, 'alert': alert_id})}"


class Engine:
    def __init__(self) -> None:
        cfg = get_config()
        self.cfg = cfg
        self.cooldown = CooldownManager(cfg["cooldown_minutes"], cfg.get("merge_window_minutes", 10))
        self.holdings: list[dict] = []
        self.portfolio_source = "none"
        self.weights: dict[str, float] = {}
        self.links: dict[str, list[dict]] = {}
        self.watchlist: set[str] = set(cfg.get("watchlist") or [])
        self.bars: dict[str, pd.DataFrame] = {}
        self.bars_source = "none"
        self.news: deque[dict] = deque(maxlen=2000)
        self._news_ids: set[str] = set()
        self.last_feed_ts: str | None = None
        self.agri_prev: dict[str, str | None] = {}
        self.last_run: dict[str, str] = {}
        self.counts = {"candidates": 0, "dropped_low_impact": 0, "new": 0, "updated": 0, "escalated": 0, "merged": 0}
        self.tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------ state
    @property
    def held(self) -> set[str]:
        return {h["ticker"] for h in self.holdings}

    def last_prices(self) -> dict[str, float]:
        return {t: float(df["close"].iloc[-1]) for t, df in self.bars.items() if len(df)}

    async def refresh_portfolio(self) -> None:
        self.holdings, self.portfolio_source = await pf.load_portfolio()
        self.links = pf.region_links()
        self.weights = pf.weights(self.holdings, self.last_prices())

    def add_news(self, items: list[dict]) -> list[dict]:
        new = []
        for it in items:
            nid = it.get("news_id") or f"{it.get('title')}|{it.get('published_at')}"
            if nid in self._news_ids:
                continue
            self._news_ids.add(nid)
            self.news.append(it)
            new.append(it)
        if len(self._news_ids) > 5000:
            self._news_ids = {it.get("news_id") or f"{it.get('title')}|{it.get('published_at')}" for it in self.news}
        return new

    # ------------------------------------------------------------------ pipeline
    async def handle(self, cands: list[Candidate]) -> list[Alert]:
        th = self.cfg["thresholds"]
        out = []
        for c in cands:
            self.counts["candidates"] += 1
            c = apply_watchlist(c, self.held, self.watchlist, th.get("watchlist_factor", 0.3))
            if c is None:
                continue
            impact = impact_score(c, self.weights)
            if impact < th.get("min_impact", 0.1):
                self.counts["dropped_low_impact"] += 1
                continue
            a = await self._route(c, impact, tier(impact, c.confidence))
            if a:
                out.append(a)
        return out

    async def _route(self, c: Candidate, impact: float, t: int) -> Alert | None:
        key = c.cooldown_key()
        decision, aid = self.cooldown.decide(key, c.kind, c.tickers, t)
        existing = await store.get(aid) if aid else None
        if decision != "new" and existing is None:
            decision = "new"
        weight = sum(self.weights.get(x, 0.0) for x in c.tickers)
        if decision == "new":
            alert = await self.build_alert(c, impact, t, weight)
            self.cooldown.record(key, alert.alert_id, c.kind, t, impact, c.tickers)
            await self.deliver(alert)
            self.counts["new"] += 1
            return alert
        if decision == "update":                       # inside the window: refresh, no re-broadcast
            existing.impact_score = max(existing.impact_score, impact) if existing.kind == c.kind else existing.impact_score
            if existing.kind == c.kind:
                existing.reason = reason_from_facts(c)
                existing.evidence_ids = list(dict.fromkeys(existing.evidence_ids + c.evidence_ids))
            await store.save(existing)
            self.counts["updated"] += 1
            return None
        if decision == "escalate":
            headline, _ = await self._headline(c, weight)
            existing.tier, existing.impact_score, existing.confidence = t, impact, c.confidence
            existing.headline = headline
            existing.reason = f"Escalated: {reason_from_facts(c)}"
            existing.evidence_ids = list(dict.fromkeys(existing.evidence_ids + c.evidence_ids))
            existing.acknowledged = False
            self.cooldown.record(key, existing.alert_id, c.kind, t, impact, c.tickers)
            await self.deliver(existing)
            self.counts["escalated"] += 1
            return existing
        # merge: price_z ↔ news_burst on the same ticker within the merge window → one alert, both facts
        new_reason = reason_from_facts(c)
        if new_reason not in existing.reason:
            existing.reason = f"{existing.reason} | {c.kind}: {new_reason}"
        rebroadcast = impact > existing.impact_score or t > existing.tier
        if impact > existing.impact_score:
            existing.kind, existing.impact_score = c.kind, impact
            existing.headline, _ = await self._headline(c, weight)
        existing.tier = max(existing.tier, t)
        existing.evidence_ids = list(dict.fromkeys(existing.evidence_ids + c.evidence_ids))
        self.cooldown.record(key, existing.alert_id, c.kind, existing.tier, impact, c.tickers)
        await (self.deliver(existing) if rebroadcast else store.save(existing))
        self.counts["merged"] += 1
        return existing if rebroadcast else None

    async def _headline(self, c: Candidate, weight: float) -> tuple[str, str]:
        headline, how = await write_headline(c, weight, **self._writer_kw())
        if (c.facts or {}).get("synthetic") and not headline.startswith("SIMULATED"):
            headline = f"SIMULATED: {headline}"           # demo data (DEMO_MODE): never presented as real
        return headline, how

    def _writer_kw(self) -> dict:
        w = self.cfg.get("writer", {})
        return {"timeout_s": w.get("timeout_s", 1.5), "max_words": w.get("max_words", 25)}

    async def build_alert(self, c: Candidate, impact: float, t: int, weight: float) -> Alert:
        headline, _ = await self._headline(c, weight)
        aid = new_alert_id()
        return Alert(alert_id=aid, tier=t, kind=c.kind, tickers=c.tickers, headline=headline,
                     reason=reason_from_facts(c), impact_score=impact, confidence=round(c.confidence, 3),
                     evidence_ids=c.evidence_ids, created_at=now_utc(), cooldown_key=c.cooldown_key(),
                     deeplink=deeplink(suggested_query(c), aid))

    async def _push_to_l1(self, alert: Alert) -> None:
        """POST the alert to the orchestrator on L1 (/alerts/ingest). Best effort: never blocks or fails delivery."""
        url = get_settings().ORCH_URL.rstrip("/") + "/alerts/ingest"
        if reachability.is_down(url):
            return
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=reachability.CONNECT_TIMEOUT_S)) as c:
                (await c.post(url, json=alert.model_dump(mode="json"))).raise_for_status()
        except (httpx.ConnectError, httpx.ConnectTimeout):
            reachability.mark_down(url)
        except httpx.HTTPError as e:
            log.debug("push to L1 failed: %s", e)

    async def deliver(self, alert: Alert) -> int:
        """Store first, then WS (never blocked by Telegram/email, which run in the background)."""
        await store.save(alert)
        n = await hub.broadcast({"type": "alert", "data": alert.model_dump(mode="json")})
        self.tasks.append(asyncio.create_task(self._push_to_l1(alert)))     # L1 keeps a copy (background)
        if alert.tier >= 2:                    # the desktop pet points at tier 2-3 alerts
            await hub.broadcast({"type": "pet_reaction", "data": {"reaction": "alert", "alert_id": alert.alert_id,
                                                                  "tier": alert.tier}})
        if alert.tier >= 3 and not alert.headline.startswith("SIMULATED"):    # demo alerts stay in the app
            telegram.enqueue(telegram.format_alert(alert))
            if email.enabled():
                body = f"{alert.headline}\n\n{alert.reason}\n\nAnalyze: {alert.deeplink}"
                self.tasks.append(asyncio.create_task(email.send_email(f"Tier 3 market alert: {', '.join(alert.tickers)}",
                                                                       body)))
        return n

    # ------------------------------------------------------------------ detector cycles
    async def run_market(self) -> list[Alert]:
        """price_z / volume_z on fresh bars only (last bar within 15 min), so stale data never alerts."""
        th = self.cfg["thresholds"]
        now = now_utc()
        fresh = {t: df for t, df in self.bars.items() if len(df) and now - df.index[-1].to_pydatetime() <= timedelta(minutes=15)}
        cands = detect_price_z(stats.price_inputs(fresh), th) + detect_volume_z(stats.volume_inputs(fresh), th)
        return await self.handle(cands)

    async def run_news(self, now: datetime | None = None) -> list[Alert]:
        th = self.cfg["thresholds"]
        now = now or now_utc()
        items = list(self.news)
        tickers = self.held | self.watchlist
        cands = (detect_news_burst(stats.news_inputs(items, tickers, now), th)
                 + detect_sentiment_shift(stats.sentiment_inputs(items, tickers, now), th))
        return await self.handle(cands)

    async def run_weather(self) -> list[Alert]:
        rows = []
        for rid in pf.regions_for(self.held | self.watchlist, self.links):
            ev = await sources.weather(rid)
            if not ev:
                continue
            v = ev.get("value") or {}
            rows.append({"region": rid, "kinds": weather_kinds(v), "confidence": ev.get("confidence"),
                         "evidence_id": ev.get("id"),
                         "facts": {**{k: v[k] for k in ("rain_anomaly_pct",) if v.get(k) is not None},
                                   **({"synthetic": True} if v.get("synthetic") or ev.get("synthetic") else {})}})
        return await self.handle(detect_weather_threshold(rows, self.links, self.cfg["severity"]["weather"],
                                                          self.held | self.watchlist))

    async def run_agri(self) -> list[Alert]:
        rows = []
        for rid in pf.regions_for(self.held | self.watchlist, self.links):
            ev = await sources.agri(rid)
            if not ev or (ev.get("degraded_reason") or "").startswith("unknown_region"):
                continue
            sc = (ev.get("value") or {}).get("stress_class")
            rows.append({"region": rid, "stress_class": sc, "prev_class": self.agri_prev.get(rid),
                         "confidence": ev.get("confidence"), "evidence_id": ev.get("id")})
            self.agri_prev[rid] = sc
        return await self.handle(detect_agri_stress(rows, self.links, self.cfg["severity"]["agri"],
                                                    self.held | self.watchlist))

    # ------------------------------------------------------------------ tasks
    async def _every(self, name: str, seconds: float, fn, first_delay: float = 0, dep: str | None = None) -> None:
        """Run fn every `seconds`. If upstream `dep` was down on this run (e.g. it was still booting when the
        monitor started), retry after RETRY_DOWN_S instead of waiting a full interval (agri's is 6 h)."""
        await asyncio.sleep(first_delay)
        while True:
            try:
                await fn()
                self.last_run[name] = now_utc().isoformat()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - a failing input never stops the monitor
                log.warning("task %s failed: %s: %s", name, type(e).__name__, e)
            down = dep is not None and sources.DEPS.get(dep) == "down"
            await asyncio.sleep(min(seconds, RETRY_DOWN_S) if down else seconds)

    async def poll_feed(self) -> None:
        got = await sources.feed_since(self.last_feed_ts)
        if got is None:
            return
        news, ticks = got
        self.add_news(news)
        if ticks:
            sources.merge_ticks(self.bars, ticks)
        self.last_feed_ts = now_utc().isoformat()
        await self.run_news()
        if ticks:
            await self.run_market()

    async def poll_prices(self, period: str = "5d") -> None:
        tickers = sorted(self.held | self.watchlist)
        if not tickers:
            return
        fresh, self.bars_source = await sources.bars_5m(tickers, period)
        for t, df in fresh.items():
            old = self.bars.get(t)
            df = df if old is None else pd.concat([old, df])
            self.bars[t] = df[~df.index.duplicated(keep="last")].sort_index().tail(80 * 61)
        self.weights = pf.weights(self.holdings, self.last_prices())
        await self.run_market()

    async def heartbeat(self) -> None:
        await hub.broadcast({"type": "heartbeat", "ts": now_utc().isoformat()})

    async def mock_cycle(self) -> None:
        """MOCK=1: the two example alerts from 11 §9 (fresh ids/timestamps), every 30 s."""
        for ex in json.loads(EXAMPLES.read_text(encoding="utf-8")):
            aid = new_alert_id()
            q = ex.pop("suggested_query")
            a = Alert(**{**ex, "alert_id": aid, "created_at": now_utc(), "deeplink": deeplink(q, aid)})
            await self.deliver(a)
            ex["suggested_query"] = q

    async def replay_feed(self, path: Path = FEED_REPLAY) -> list[Alert]:
        """Push the recorded feed (timestamps relative to now: minutes_ago) through the real news detectors."""
        if not path.exists():
            return []
        now = now_utc()
        items = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                it = json.loads(line)
                it["published_at"] = (now - timedelta(minutes=float(it.pop("minutes_ago", 0)))).isoformat()
                items.append(it)
        self.add_news(items)
        return await self.run_news(now)

    async def start(self) -> None:
        iv = self.cfg["intervals_s"]
        await store.init_db()
        await self.refresh_portfolio()
        self.tasks.append(asyncio.create_task(telegram.worker()))
        self.tasks.append(asyncio.create_task(self._every("heartbeat", iv["heartbeat"], self.heartbeat, iv["heartbeat"])))
        if get_settings().MOCK:
            self.tasks.append(asyncio.create_task(self._every("replay", 10 ** 9, self.replay_feed)))
            self.tasks.append(asyncio.create_task(self._every("mock", iv["mock"], self.mock_cycle, iv.get("mock_first", 2))))
            return
        self.tasks += [
            asyncio.create_task(self._every("portfolio", iv["portfolio"], self.refresh_portfolio, iv["portfolio"])),
            asyncio.create_task(self._every("baselines", iv["baselines"], lambda: self.poll_prices("30d"), dep="prices")),
            asyncio.create_task(self._every("prices", iv["prices"], self.poll_prices, iv["prices"])),
            asyncio.create_task(self._every("feed", iv["feed"], self.poll_feed, 1)),
            asyncio.create_task(self._every("weather", iv["weather"], self.run_weather, 3, dep="ingestion")),
            asyncio.create_task(self._every("agri", iv["agri"], self.run_agri, 5, dep="agri")),
        ]

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()

    def state(self) -> dict:
        return {"mock": get_settings().MOCK, "portfolio_source": self.portfolio_source, "holdings": len(self.holdings),
                "bars_source": self.bars_source, "bars": {t: len(df) for t, df in self.bars.items()},
                "news_buffer": len(self.news), "last_run": self.last_run, "deps": dict(sources.DEPS),
                "delivery": {"ws_clients": len(hub.clients), "telegram": telegram.STATUS, "email": email.STATUS},
                "active_cooldowns": self.cooldown.snapshot(), "counts": self.counts,
                "detectors": ["price_z", "volume_z", "news_burst", "sentiment_shift", "weather_threshold", "agri_stress"],
                "thresholds": self.cfg["thresholds"]}


engine = Engine()
