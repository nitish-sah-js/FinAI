"""Monitor service (11 §8): WS /ws/alerts, GET /alerts, POST /alerts/{id}/ack, POST /alerts/test, GET /monitor/state."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

import httpx
from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from copilot_common import reachability
from copilot_common.ids import new_alert_id
from copilot_common.models import Alert
from copilot_common.service_base import create_service_app, now_utc
from copilot_common.settings import get_settings

from . import sources, store
from .delivery import email, telegram
from .delivery.ws_hub import hub
from .detectors import Candidate
from .engine import deeplink, engine

Kind = Literal["price_z", "volume_z", "news_burst", "sentiment_shift", "weather_threshold", "agri_stress"]


@asynccontextmanager
async def lifespan(app):
    await engine.start()
    yield
    await engine.stop()


async def deps_check() -> dict[str, str]:
    """ingestion / agri from the last poll; ollama_l3 by a short ping; delivery channels when configured."""
    s = get_settings()
    deps = {k: v for k, v in sources.DEPS.items() if v != "unknown"}
    if s.MOCK:
        return {"mode": "ok"}
    url = s.OLLAMA_L3.rstrip("/") + "/api/version"
    if reachability.is_down(url):
        deps["ollama_l3"] = "down"
    else:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=reachability.CONNECT_TIMEOUT_S)) as c:
                deps["ollama_l3"] = "ok" if (await c.get(url)).status_code == 200 else "down"
        except httpx.HTTPError:
            reachability.mark_down(url)
            deps["ollama_l3"] = "down"
    for name, st in (("telegram", telegram.STATUS), ("email", email.STATUS)):
        if st["state"] != "disabled":
            deps[name] = st["state"]
    return deps


app = create_service_app("monitor", deps_check=deps_check, models=[get_settings().OLLAMA_MODEL_L3_FAST],
                         lifespan=lifespan)


@app.websocket("/ws/alerts")
async def ws_alerts(ws: WebSocket):
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()           # clients may send pings; nothing else is expected
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(ws)


@app.get("/alerts", response_model=list[Alert])
async def get_alerts(limit: int = 50, tier_min: int = 1, since: datetime | None = None):
    return await store.list_alerts(min(max(limit, 1), 500), tier_min, since)


@app.post("/alerts/{alert_id}/ack", response_model=Alert)
async def ack_alert(alert_id: str):
    a = await store.ack(alert_id)
    if a is None:
        raise HTTPException(404, f"unknown alert {alert_id}")
    hub.mark_acked(alert_id)
    await hub.broadcast({"type": "ack", "alert_id": alert_id})
    return a


class TestAlertReq(BaseModel):
    kind: Kind = "weather_threshold"
    tickers: list[str] = Field(default_factory=lambda: ["ONGC.NS"])
    tier: Literal[1, 2, 3] = 3
    region: str = "OD-Puri"


@app.post("/alerts/test", response_model=Alert)
async def test_alert(req: TestAlertReq | None = None):
    """Demo knob: inject a synthetic alert (reason "TEST"), bypassing cooldown, delivered like any other."""
    req = req or TestAlertReq()
    impact = {1: 0.2, 2: 0.45, 3: 0.75}[req.tier]
    c = Candidate(kind=req.kind, tickers=req.tickers, severity=impact, relevance=1.0, confidence=0.8,
                  facts={"test": True}, key=f"test:{req.region}")
    aid = new_alert_id()
    a = Alert(alert_id=aid, tier=req.tier, kind=req.kind, tickers=req.tickers,
              headline=f"TEST alert for {', '.join(req.tickers)} (tier {req.tier}).", reason="TEST",
              impact_score=impact, confidence=0.8, created_at=now_utc(), cooldown_key=c.cooldown_key(),
              deeplink=deeplink(f"What is happening with {req.tickers[0]} and should I act?", aid))
    await engine.deliver(a)
    return a


@app.get("/monitor/state")
async def monitor_state():
    return {**engine.state(), "stored": await store.counts()}
