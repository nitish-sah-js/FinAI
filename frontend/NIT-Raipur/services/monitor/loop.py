import asyncio
from datetime import datetime
from store import save_alert
from delivery.ws_hub import hub
from delivery.email import send_email
from copilot_common import Alert, settings, new_alert_id

async def deliver_alert(alert: Alert):
    # 1. Save to SQLite
    await save_alert(alert)
    
    # 2. Broadcast via WS to all clients
    await hub.broadcast({"type": "alert", "data": alert.model_dump(), "replay": False})
    
    # 3. For Tier 3, send to Email if configured
    if alert.tier >= 3:
        # We don't await these so delivery failures don't block the loop
        email_body = f"Tier {alert.tier} Market Alert\n\nHeadline: {alert.headline}\nReason: {alert.reason}\n\nAnalyze: {alert.deeplink}"
        asyncio.create_task(send_email("🚨 High Priority Market Alert", email_body))

async def mock_alert_loop():
    """When MOCK=1, emits the two example alerts every 30 seconds."""
    tier3_mock = {
        "tier": 3,
        "kind": "weather_threshold",
        "tickers": ["ADANIPORTS.NS", "ONGC.NS"],
        "headline": "Very severe cyclone 310 km off Puri threatens Paradip; ports and ONGC exposure is 22% of portfolio; confidence medium.",
        "reason": "OD-Puri alerts [cyclone, heavy_rain]; rain +184% vs normal; link strength ADANIPORTS 0.8, ONGC 0.5",
        "impact_score": 0.66,
        "confidence": 0.75,
        "evidence_ids": ["ev_weather_001"],
        "cooldown_key": "weather_threshold:OD-Puri",
        "deeplink": f"http://localhost:3000/run/new?q=How+does+the+Odisha+cyclone+affect+my+portfolio+over+5+days%3F&alert=MOCK",
        "acknowledged": False
    }
    
    tier1_mock = {
        "tier": 1,
        "kind": "news_burst",
        "tickers": ["ITC.NS"],
        "headline": "ITC.NS: 5 headlines in the last hour versus about 1 normally; 6% of portfolio; confidence medium.",
        "reason": "k=5, lambda=0.9, z=4.3",
        "impact_score": 0.24,
        "confidence": 0.6,
        "evidence_ids": [],
        "cooldown_key": "news_burst:ITC.NS",
        "deeplink": f"http://localhost:3000/run/new?q=What+is+the+news+on+ITC+and+does+it+matter%3F&alert=MOCK",
        "acknowledged": False
    }

    while True:
        await asyncio.sleep(30)
        
        # Emit Tier 3 Alert
        a1 = Alert(alert_id=new_alert_id(), created_at=datetime.utcnow(), **tier3_mock)
        await deliver_alert(a1)
        
        await asyncio.sleep(2)
        
        # Emit Tier 1 Alert
        a2 = Alert(alert_id=new_alert_id(), created_at=datetime.utcnow(), **tier1_mock)
        await deliver_alert(a2)

async def run_loop():
    if settings.mock == 1:
        print("Starting MOCK=1 loop (emitting fake alerts every 30s)...")
        asyncio.create_task(mock_alert_loop())
    else:
        print("Starting production polling loop...")
        
    while True:
        await asyncio.sleep(15)
        # Production data polling would hit Ingestion/Agri/Weather services here.
