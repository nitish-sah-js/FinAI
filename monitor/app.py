import asyncio
from fastapi import WebSocket
from copilot_common import create_service_app, Alert, new_alert_id, settings
from store import init_db, save_alert
from loop import run_loop
from delivery.ws_hub import hub
from urllib.parse import urlencode

app = create_service_app("monitor")

@app.on_event("startup")
async def startup():
    await init_db()
    asyncio.create_task(run_loop())

@app.websocket("/ws/alerts")
async def ws_alerts(websocket: WebSocket):
    await hub.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except:
        hub.disconnect(websocket)

@app.get("/alerts")
async def get_alerts():
    return []

@app.post("/alerts/{alert_id}/ack")
async def ack_alert(alert_id: str):
    return {"acknowledged": True}

@app.post("/alerts/test")
async def test_alert(payload: dict):
    from datetime import datetime
    alert = Alert(
        alert_id=new_alert_id(),
        tier=payload.get("tier", 3),
        kind=payload.get("kind", "weather_threshold"),
        tickers=payload.get("tickers", ["ONGC.NS"]),
        headline="TEST: Severe weather alert injected via demo endpoint.",
        reason="TEST",
        impact_score=0.9,
        confidence=1.0,
        evidence_ids=[],
        created_at=datetime.utcnow(),
        cooldown_key="test:alert",
        deeplink=f"http://localhost:3000/run/new?q={urlencode({'q': 'Why is ONGC moving?'})}&alert=test",
        acknowledged=False
    )
    await save_alert(alert)
    await hub.broadcast({"type": "alert", "data": alert.model_dump(), "replay": False})
    return alert
