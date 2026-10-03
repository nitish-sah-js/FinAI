"""SQLite data/alerts.db (11 §8): alerts(alert_id PK, json, created_at, tier, kind, cooldown_key, acknowledged)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import aiosqlite

from copilot_common.models import Alert
from copilot_common.settings import get_data_dir

_path: Path | None = None


def db_path() -> Path:
    return _path or get_data_dir() / "alerts.db"


def set_path(p: Path | None) -> None:          # tests
    global _path
    _path = p


async def init_db() -> None:
    async with aiosqlite.connect(db_path()) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS alerts (alert_id TEXT PRIMARY KEY, json TEXT, created_at TEXT, "
                         "tier INTEGER, kind TEXT, cooldown_key TEXT, acknowledged INTEGER)")
        await db.execute("CREATE INDEX IF NOT EXISTS alerts_created ON alerts(created_at)")
        await db.commit()


async def save(alert: Alert) -> None:
    """Insert or update in place (cooldown updates/escalations keep the same alert_id)."""
    async with aiosqlite.connect(db_path()) as db:
        await db.execute("INSERT OR REPLACE INTO alerts VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (alert.alert_id, alert.model_dump_json(), alert.created_at.isoformat(), alert.tier,
                          alert.kind, alert.cooldown_key, int(alert.acknowledged)))
        await db.commit()


async def get(alert_id: str) -> Alert | None:
    async with aiosqlite.connect(db_path()) as db:
        cur = await db.execute("SELECT json FROM alerts WHERE alert_id = ?", (alert_id,))
        row = await cur.fetchone()
    return Alert.model_validate_json(row[0]) if row else None


async def list_alerts(limit: int = 50, tier_min: int = 1, since: datetime | None = None) -> list[Alert]:
    q, args = "SELECT json FROM alerts WHERE tier >= ?", [tier_min]
    if since:
        q += " AND created_at >= ?"
        args.append(since.isoformat())
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    async with aiosqlite.connect(db_path()) as db:
        cur = await db.execute(q, args)
        rows = await cur.fetchall()
    return [Alert.model_validate_json(r[0]) for r in rows]


async def ack(alert_id: str) -> Alert | None:
    a = await get(alert_id)
    if a is None:
        return None
    a.acknowledged = True
    await save(a)
    return a


async def counts() -> dict:
    async with aiosqlite.connect(db_path()) as db:
        cur = await db.execute("SELECT tier, COUNT(*) FROM alerts GROUP BY tier")
        rows = await cur.fetchall()
    return {f"tier_{t}": n for t, n in rows}
