"""Paper-trading tables (12 §B2) in the orchestrator's data/ledger.db, next to the run ledger."""
from __future__ import annotations

import os
import threading

import aiosqlite

from copilot_common.settings import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_proposals (
  proposal_id TEXT PRIMARY KEY, run_id TEXT, hedge_json TEXT,
  status TEXT CHECK(status IN ('pending','approved','rejected','expired')),
  created_at TEXT, decided_at TEXT, approved_by TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS paper_positions (
  position_id TEXT PRIMARY KEY, proposal_id TEXT, instrument TEXT, underlying TEXT, side TEXT,
  quantity REAL, unit TEXT, entry_price REAL, entry_ts TEXT,
  price_kind TEXT,  -- 'close' | 'spot_proxy' | 'model_price'
  status TEXT CHECK(status IN ('open','closed')), exit_price REAL, exit_ts TEXT);
CREATE TABLE IF NOT EXISTS paper_marks (
  position_id TEXT, ts TEXT, mark_price REAL, pnl_inr REAL, portfolio_pnl_unhedged_inr REAL,
  portfolio_pnl_hedged_inr REAL, PRIMARY KEY (position_id, ts));
-- portfolio prices at approval, so marks can compute hedged vs unhedged P&L since entry (12 §B3)
CREATE TABLE IF NOT EXISTS paper_snapshots (position_id TEXT PRIMARY KEY, snapshot_json TEXT);
"""


def db_path() -> str:
    return os.getenv("LEDGER_DB") or str(get_settings().data_dir / "ledger.db")


async def connect() -> aiosqlite.Connection:
    """A short-lived connection per request (SQLite handles the concurrency; the run ledger uses its own)."""
    conn = aiosqlite.connect(db_path())
    if isinstance(getattr(conn, "_thread", None), threading.Thread):
        conn._thread.daemon = True
    db = await conn
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA)
    # the orchestrator ledger creates `runs`; make sure it exists on a fresh DB (no-op otherwise)
    from ..ledger import SCHEMA as LEDGER_SCHEMA
    await db.executescript(LEDGER_SCHEMA)
    await db.commit()
    return db
