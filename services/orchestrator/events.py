"""EventBus: AgentEvent → ledger + WebSocket subscribers (04 §9 step 2, 01 §8)."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import datetime, timezone

from copilot_common.models import AgentEvent

from .ledger import ledger

NODE_HOST = {"parse_intent": "L1", "router": "L1", "join": "L1", "planner": "L1", "synthesizer": "L1",
             "validator": "L1", "explain": "L1", "quant_agent": "L2", "exposure_agent": "L2",
             "sentiment_agent": "L2", "agri_agent": "L2", "analog_agent": "L2", "weather_agent": "L3",
             "macro_agent": "L3", "red_team": "L3"}


class EventBus:
    def __init__(self) -> None:
        self._seq: dict[str, int] = defaultdict(int)
        self._history: dict[str, list[dict]] = defaultdict(list)
        self._subs: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._activity: list[asyncio.Queue] = []
        self._t0: dict[str, float] = {}
        self._done: set[str] = set()
        self.marks: dict[str, dict[str, float]] = defaultdict(dict)

    def start_run(self, run_id: str) -> None:
        self._t0.setdefault(run_id, time.perf_counter())
        self._done.discard(run_id)

    def t_ms(self, run_id: str) -> int:
        return int((time.perf_counter() - self._t0.get(run_id, time.perf_counter())) * 1000)

    def mark(self, run_id: str, name: str) -> None:
        self.marks[run_id][name] = time.perf_counter()

    def since_mark_ms(self, run_id: str, name: str) -> int | None:
        t = self.marks[run_id].get(name)
        return int((time.perf_counter() - t) * 1000) if t else None

    async def _push(self, run_id: str, msg: dict) -> None:
        self._history[run_id].append(msg)
        for q in list(self._subs[run_id]):
            q.put_nowait(msg)
        for q in list(self._activity):
            q.put_nowait(msg)

    async def emit(self, run_id: str, node: str, status: str, **kw) -> AgentEvent:
        self._seq[run_id] += 1
        kw.setdefault("host", NODE_HOST.get(node))
        t = self.t_ms(run_id)
        kw.setdefault("t_ms", t)
        ev = AgentEvent(run_id=run_id, seq=self._seq[run_id], node=node, status=status,
                        ts=datetime.now(timezone.utc), **kw)
        msg = {"type": "event", "data": ev.model_dump(mode="json")}
        await self._push(run_id, msg)
        try:
            await ledger.add_event(run_id, ev.seq, t, msg)
        except Exception:  # noqa: BLE001  the ledger must never break a run
            pass
        return ev

    async def publish_final(self, run_id: str, final: dict) -> None:
        msg = {"type": "final", "data": final}
        try:
            await ledger.add_event(run_id, 10**6, self.t_ms(run_id), msg)
        except Exception:  # noqa: BLE001
            pass
        await self._push(run_id, msg)
        self._done.add(run_id)

    async def subscribe(self, run_id: str):
        """Yield past messages first (late subscribers), then live ones, until the final message."""
        q: asyncio.Queue = asyncio.Queue()
        history = list(self._history.get(run_id, []))
        if not history:
            history = [m for _, m in await ledger.get_events(run_id)]
        self._subs[run_id].append(q)
        try:
            for m in history:
                yield m
                if m["type"] == "final":
                    return
            if run_id in self._done:
                return
            while True:
                m = await q.get()
                yield m
                if m["type"] == "final":
                    return
        finally:
            self._subs[run_id].remove(q)

    async def subscribe_activity(self):
        q: asyncio.Queue = asyncio.Queue()
        self._activity.append(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._activity.remove(q)

    def forget(self, run_id: str) -> None:
        """Drop in-memory history of a finished run (the ledger keeps it)."""
        self._history.pop(run_id, None)
        self.marks.pop(run_id, None)


bus = EventBus()
