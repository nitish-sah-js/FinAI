import os

def write_file(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

base = "d:/Some_stuffs/Codeutsava X.0/NIT-Raipur/services/monitor"

write_file(f"{base}/requirements.txt", """fastapi
uvicorn
httpx
aiosqlite
pydantic
pydantic-settings
pandas
""")

write_file(f"{base}/copilot_common.py", """from pydantic import BaseModel, Field
from datetime import datetime
from typing import Any, Literal
import uuid

class Evidence(BaseModel):
    id: str
    run_id: str | None = None
    tool: str
    value: dict[str, Any]
    summary: str | None = None
    source: str
    source_url: str | None = None
    as_of: datetime
    timestamp: datetime
    freshness_s: int | None = None
    confidence: float | None = Field(None, ge=0, le=1)
    degraded: bool = False
    degraded_reason: str | None = None
    latency_ms: int | None = None
    model_version: str | None = None
    staleness_factor: float | None = None

class Alert(BaseModel):
    alert_id: str
    tier: int
    kind: str
    tickers: list[str]
    headline: str
    reason: str
    impact_score: float
    confidence: float
    evidence_ids: list[str]
    created_at: datetime
    cooldown_key: str
    deeplink: str
    acknowledged: bool = False

class Health(BaseModel):
    status: str
    deps: dict[str, str] = {}

class Settings(BaseModel):
    ingest_url: str = "http://localhost:8201"
    agri_url: str = "http://localhost:8103"
    orch_url: str = "http://localhost:8000"
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    smtp_user: str | None = None
    smtp_app_password: str | None = None
    alert_email_to: str | None = None
    mock: int = 0

settings = Settings()

def new_alert_id():
    return f"al_{datetime.utcnow().strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"

def create_service_app(name, deps_check=None):
    from fastapi import FastAPI
    app = FastAPI(title=name)
    @app.get("/health")
    def health():
        return Health(status="ok")
    return app

class copilot_llm:
    @staticmethod
    async def chat(role, messages, max_tokens=60):
        # Stub for LLM
        return "Stub headline from LLM"
""")

write_file(f"{base}/config.yaml", """thresholds:
  price_z: 3.0
  volume_z: 3.0
  news_burst_z: 3.0
  news_burst_k: 3
  sentiment_shift: 0.4
  sentiment_min_n: 3
""")

write_file(f"{base}/portfolio.py", """import pandas as pd
import json

def load_portfolio():
    # Stub: load weights from CSV or Orchestrator
    # Returns dict[ticker, float] weights
    return {"RELIANCE.NS": 0.12, "TCS.NS": 0.08, "ITC.NS": 0.06, "ONGC.NS": 0.04, "ADANIPORTS.NS": 0.05}

def region_links():
    # Stub: load from region_exposure.json
    return {"OD-Puri": [("ADANIPORTS.NS", 0.8), ("ONGC.NS", 0.5)]}
""")

write_file(f"{base}/detectors/__init__.py", "from pydantic import BaseModel\n\nclass Candidate(BaseModel):\n    kind: str\n    tickers: list[str]\n    severity: float\n    relevance: float\n    confidence: float\n    facts: dict\n    evidence_ids: list[str]\n")

write_file(f"{base}/detectors/price_volume.py", """from . import Candidate

def detect_price_z(prices_df):
    return []

def detect_volume_z(volumes_df):
    return []
""")

write_file(f"{base}/detectors/news_burst.py", """from . import Candidate

def detect_news_burst(news_df):
    return []
""")

write_file(f"{base}/detectors/sentiment.py", """from . import Candidate

def detect_sentiment_shift(sentiment_df):
    return []
""")

write_file(f"{base}/detectors/weather.py", """from . import Candidate

def detect_weather_threshold(weather_data, region_links):
    return []
""")

write_file(f"{base}/detectors/agri.py", """from . import Candidate

def detect_agri_stress(agri_data, region_links):
    return []
""")

write_file(f"{base}/scoring.py", """from detectors import Candidate

def impact_score(c: Candidate, weights: dict[str, float]) -> float:
    exposure = min(1.0, sum(weights.get(t, 0.0) for t in c.tickers) / 0.25)
    return round(min(1.0, c.severity * c.relevance * (0.4 + 0.6 * exposure)), 3)

def tier(impact: float, confidence: float) -> int:
    if impact >= 0.6 and confidence >= 0.6: return 3
    if impact >= 0.3: return 2
    return 1
""")

write_file(f"{base}/dedupe.py", """class CooldownManager:
    def __init__(self):
        self.active = {}
    def should_broadcast(self, key, tier):
        # Stub
        return True
""")

write_file(f"{base}/writer.py", """from copilot_common import copilot_llm

async def write_headline(c, weight):
    try:
        return await copilot_llm.chat(role="alert_writer", messages=[{"role":"user","content":str(c.facts)}], max_tokens=60)
    except Exception:
        return f"{c.tickers[0]}: {c.kind} ({c.facts}); {weight:.0%} of portfolio; confidence {c.confidence}."

def reason_from_facts(c):
    return str(c.facts)
""")

write_file(f"{base}/delivery/__init__.py", "")
write_file(f"{base}/delivery/ws_hub.py", """import json

class WSHub:
    def __init__(self):
        self.clients = set()
        self.history = []
    async def connect(self, ws):
        await ws.accept()
        self.clients.add(ws)
    def disconnect(self, ws):
        self.clients.remove(ws)
    async def broadcast(self, data):
        self.history.append(data)
        for client in self.clients:
            await client.send_text(json.dumps(data))

hub = WSHub()
""")

write_file(f"{base}/delivery/telegram.py", """async def send_telegram(text):
    pass
""")

write_file(f"{base}/delivery/email.py", """async def send_email(subject, body):
    pass
""")

write_file(f"{base}/store.py", """import aiosqlite
import json

async def init_db():
    import os
    os.makedirs("data", exist_ok=True)
    async with aiosqlite.connect("data/alerts.db") as db:
        await db.execute("CREATE TABLE IF NOT EXISTS alerts (alert_id TEXT PRIMARY KEY, json TEXT, created_at TEXT, tier INTEGER, kind TEXT, cooldown_key TEXT, acknowledged BOOLEAN)")
        await db.commit()

async def save_alert(alert):
    async with aiosqlite.connect("data/alerts.db") as db:
        await db.execute("INSERT INTO alerts VALUES (?, ?, ?, ?, ?, ?, ?)", (alert.alert_id, alert.model_dump_json(), alert.created_at.isoformat(), alert.tier, alert.kind, alert.cooldown_key, alert.acknowledged))
        await db.commit()
""")

write_file(f"{base}/loop.py", """import asyncio

async def run_loop():
    while True:
        await asyncio.sleep(15)
        # Main monitoring loop stub
""")

write_file(f"{base}/app.py", """import asyncio
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
""")

print("Monitor service built successfully.")
