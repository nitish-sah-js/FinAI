"""WS fan-out for /ws/alerts: replay the last 10 alerts on connect, broadcast live, heartbeat every 20 s.
One slow or broken client never blocks the others."""
from __future__ import annotations

import asyncio
import json
from collections import deque

from fastapi import WebSocket


class WSHub:
    def __init__(self, replay: int = 10):
        self.clients: set[WebSocket] = set()
        self.recent: deque[dict] = deque(maxlen=replay)      # last alert messages (data = Alert json)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        for msg in list(self.recent):
            await ws.send_text(json.dumps({**msg, "replay": True}, default=str))
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    def remember(self, msg: dict) -> None:
        if msg.get("type") == "alert":
            aid = msg["data"]["alert_id"]
            for i, m in enumerate(self.recent):          # an escalation replaces the older copy
                if m["data"]["alert_id"] == aid:
                    del self.recent[i]
                    break
            self.recent.append(msg)

    async def broadcast(self, msg: dict) -> int:
        """Send to every client; returns how many received it. Dead clients are dropped."""
        self.remember(msg)
        text = json.dumps(msg, default=str)

        async def send(ws: WebSocket) -> bool:
            try:
                await asyncio.wait_for(ws.send_text(text), 2.0)
                return True
            except Exception:  # noqa: BLE001
                self.disconnect(ws)
                return False

        results = await asyncio.gather(*(send(ws) for ws in list(self.clients)))
        return sum(results)

    def mark_acked(self, alert_id: str) -> None:
        for m in self.recent:
            if m.get("type") == "alert" and m["data"]["alert_id"] == alert_id:
                m["data"]["acknowledged"] = True


hub = WSHub()
