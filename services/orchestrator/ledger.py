"""SQLite ledger: runs, events, evidence (04 §7)."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from copilot_common.settings import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, created_at TEXT, query TEXT, status TEXT,
                                 confidence TEXT, request_json TEXT, final_json TEXT);
CREATE TABLE IF NOT EXISTS events (run_id TEXT, seq INTEGER, t_ms INTEGER, json TEXT, PRIMARY KEY (run_id, seq));
CREATE TABLE IF NOT EXISTS evidence (id TEXT, run_id TEXT, tool TEXT, json TEXT, PRIMARY KEY (run_id, id));
CREATE TABLE IF NOT EXISTS trace (run_id TEXT, ts TEXT, node TEXT, service TEXT, host TEXT, model TEXT,
                                  latency_ms INTEGER, status TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS trace_run ON trace (run_id);
"""
TRACE_COLS = ("run_id", "ts", "node", "service", "host", "model", "latency_ms", "status", "detail")


class Ledger:
    def __init__(self, path: Path | None = None):
        self._path = path
        self._db: aiosqlite.Connection | None = None

    @property
    def path(self) -> Path:
        return self._path or get_settings().data_dir / "ledger.db"

    async def db(self) -> aiosqlite.Connection:
        if self._db is None:
            conn = aiosqlite.connect(self.path)
            if isinstance(getattr(conn, "_thread", None), threading.Thread):
                conn._thread.daemon = True        # never keep the process alive (scripts / backtest that forget close())
            self._db = await conn
            self._db.row_factory = aiosqlite.Row
            await self._db.executescript(SCHEMA)
            await self._db.commit()
        return self._db

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def create_run(self, run_id: str, query: str, request: dict) -> None:
        db = await self.db()
        await db.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?)",
                         (run_id, datetime.now(timezone.utc).isoformat(), query, "running", None,
                          json.dumps(request, default=str), None))
        await db.commit()

    async def set_status(self, run_id: str, status: str) -> None:
        db = await self.db()
        await db.execute("UPDATE runs SET status=? WHERE run_id=?", (status, run_id))
        await db.commit()

    async def save_final(self, run_id: str, final: dict) -> None:
        db = await self.db()
        await db.execute("UPDATE runs SET status=?, confidence=?, final_json=? WHERE run_id=?",
                         ("done", final.get("confidence"), json.dumps(final, default=str), run_id))
        for ev in final.get("evidence", []):
            await db.execute("INSERT OR REPLACE INTO evidence VALUES (?,?,?,?)",
                             (ev["id"], run_id, ev["tool"], json.dumps(ev, default=str)))
        await db.commit()

    async def add_event(self, run_id: str, seq: int, t_ms: int, event: dict) -> None:
        db = await self.db()
        await db.execute("INSERT OR REPLACE INTO events VALUES (?,?,?,?)", (run_id, seq, t_ms, json.dumps(event, default=str)))
        await db.commit()

    async def add_trace(self, row: dict) -> None:
        db = await self.db()
        await db.execute(f"INSERT INTO trace VALUES ({','.join('?' * len(TRACE_COLS))})",
                         tuple(row.get(c) for c in TRACE_COLS))
        await db.commit()

    async def get_trace(self, run_ids: list[str]) -> list[dict]:
        db = await self.db()
        q = f"SELECT * FROM trace WHERE run_id IN ({','.join('?' * len(run_ids))}) ORDER BY ts"
        async with db.execute(q, tuple(run_ids)) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_run(self, run_id: str) -> dict | None:
        db = await self.db()
        async with db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return {"run_id": row["run_id"], "created_at": row["created_at"], "query": row["query"], "status": row["status"],
                "confidence": row["confidence"], "request": json.loads(row["request_json"] or "{}"),
                "final": json.loads(row["final_json"]) if row["final_json"] else None}

    async def get_events(self, run_id: str) -> list[tuple[int, dict]]:
        db = await self.db()
        async with db.execute("SELECT t_ms, json FROM events WHERE run_id=? ORDER BY seq", (run_id,)) as cur:
            return [(r["t_ms"], json.loads(r["json"])) for r in await cur.fetchall()]

    async def list_runs(self, limit: int = 20) -> list[dict]:
        db = await self.db()
        async with db.execute("SELECT run_id, query, created_at, status, confidence FROM runs "
                              "ORDER BY created_at DESC LIMIT ?", (limit,)) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def latest_done_run(self, exclude: str | None = None) -> str | None:
        db = await self.db()
        async with db.execute("SELECT run_id FROM runs WHERE status='done' AND run_id != ? "
                              "ORDER BY created_at DESC LIMIT 1", (exclude or "",)) as cur:
            row = await cur.fetchone()
        return row["run_id"] if row else None


ledger = Ledger()
