import aiosqlite
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
