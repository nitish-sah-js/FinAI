"""Paper-trading router, mounted at /paper by orchestrator/app.py (12 §B3).
No real orders; approval requires a human (approved_by)."""
from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from copilot_common.prices import PriceUnavailable

from . import pricing
from .db import connect

router = APIRouter()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id(prefix: str) -> str:
    return f"{prefix}_{datetime.now(timezone.utc):%Y%m%d}_{secrets.token_hex(2)}"


class ProposeIn(BaseModel):
    run_id: str
    hedge_id: str


class ApproveIn(BaseModel):
    proposal_id: str
    decision: Literal["approve", "reject"]
    approved_by: str
    note: Optional[str] = None

    @field_validator("approved_by")
    @classmethod
    def _nonblank(cls, v):
        if not v.strip():
            raise ValueError("approved_by is required")
        return v


class CloseIn(BaseModel):
    position_id: str


async def _rows(db, sql, args=()):
    cur = await db.execute(sql, args)
    return [dict(r) for r in await cur.fetchall()]


async def _load_holdings(db, run_id: str | None) -> list[dict]:
    """The portfolio the run was asked about: the request's own portfolio if it had one, else the stored demo one.
    (FinalAnswer carries no portfolio, 01 §5.)"""
    if pricing._mock():
        return [{"ticker": "HDFCBANK.NS", "qty": 100}]
    try:
        rows = await _rows(db, "SELECT request_json FROM runs WHERE run_id=?", (run_id,))
        pf = json.loads(rows[0]["request_json"] or "{}").get("portfolio") if rows else None
        if pf and pf.get("holdings"):
            return pf["holdings"]
    except Exception:  # noqa: BLE001  older ledgers have no request_json column
        pass
    from ..portfolio import load
    try:
        return [h.model_dump() for h in load("demo").holdings]
    except KeyError:
        return []


async def snapshot_portfolio(db, run_id: str | None) -> dict:
    snap, skipped = [], []
    for h in await _load_holdings(db, run_id):
        try:
            px = await asyncio.to_thread(pricing.latest_close, h["ticker"])
        except Exception:  # noqa: BLE001
            skipped.append(h["ticker"])
            continue
        snap.append({"ticker": h["ticker"], "qty": h["qty"], "entry_price": px})
    return {"holdings": snap, "skipped": skipped}


@router.post("/propose")
async def propose(body: ProposeIn):
    db = await connect()
    try:
        runs = await _rows(db, "SELECT final_json FROM runs WHERE run_id=?", (body.run_id,))
        if not runs or not runs[0]["final_json"]:
            raise HTTPException(404, "run not found")
        final = json.loads(runs[0]["final_json"])
        hedge = next((h for h in final.get("hedges", []) if h.get("hedge_id") == body.hedge_id), None)
        if hedge is None:
            raise HTTPException(404, "hedge_id not found in run")
        pid = _id("pp")
        # the hedge is copied EXACTLY from the stored FinalAnswer, never resized (12 §B3)
        await db.execute("INSERT INTO paper_proposals VALUES (?,?,?,?,?,?,?,?)",
                         (pid, body.run_id, json.dumps(hedge), "pending", _now(), None, None, None))
        await db.commit()
        return (await _rows(db, "SELECT * FROM paper_proposals WHERE proposal_id=?", (pid,)))[0]
    finally:
        await db.close()


@router.post("/approve")
async def approve(body: ApproveIn):
    db = await connect()
    try:
        rows = await _rows(db, "SELECT * FROM paper_proposals WHERE proposal_id=?", (body.proposal_id,))
        if not rows:
            raise HTTPException(404, "proposal not found")
        prop = rows[0]
        if prop["status"] != "pending":
            raise HTTPException(409, f"proposal already {prop['status']}")
        status = "approved" if body.decision == "approve" else "rejected"
        hedge = json.loads(prop["hedge_json"])
        price = kind = None
        if status == "approved":
            # price + lot size BEFORE any write: if either is unavailable the proposal stays pending
            try:
                if hedge.get("unit", "lots") == "lots":
                    pricing.lot_size(hedge["instrument"])
                price, kind = await asyncio.to_thread(pricing.mark_price, hedge)
            except pricing.LotSizeUnknown as e:
                raise HTTPException(422, str(e))
            except PriceUnavailable as e:
                raise HTTPException(503, f"no price for {hedge['underlying']}: {e}")
        ts = _now()
        await db.execute("UPDATE paper_proposals SET status=?, decided_at=?, approved_by=?, note=? WHERE proposal_id=?",
                         (status, ts, body.approved_by, body.note, body.proposal_id))
        position = None
        if status == "approved":
            pos_id = _id("pos")
            await db.execute("INSERT INTO paper_positions (position_id, proposal_id, instrument, underlying, side, "
                             "quantity, unit, entry_price, entry_ts, price_kind, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (pos_id, prop["proposal_id"], hedge["instrument"], hedge["underlying"], hedge["side"],
                              hedge["quantity"], hedge.get("unit", "lots"), price, ts, kind, "open"))
            snap = await snapshot_portfolio(db, prop["run_id"])
            await db.execute("INSERT INTO paper_snapshots VALUES (?,?)", (pos_id, json.dumps(snap)))
            position = (await _rows(db, "SELECT * FROM paper_positions WHERE position_id=?", (pos_id,)))[0]
        await db.commit()
        proposal = (await _rows(db, "SELECT * FROM paper_proposals WHERE proposal_id=?", (body.proposal_id,)))[0]
        return {"proposal": proposal, "position": position}
    finally:
        await db.close()


async def mark_positions(db) -> list[dict]:
    """Write one mark per open position; compute hedged vs unhedged portfolio P&L since entry.
    A position whose price is unavailable is reported, not fatal for the others."""
    out = []
    for p in await _rows(db, "SELECT * FROM paper_positions WHERE status='open'"):
        hedge = {"instrument": p["instrument"], "underlying": p["underlying"]}
        prop = await _rows(db, "SELECT hedge_json FROM paper_proposals WHERE proposal_id=?", (p["proposal_id"],))
        if prop:
            hedge = json.loads(prop[0]["hedge_json"])
        try:
            price, _ = await asyncio.to_thread(pricing.mark_price, hedge)
            pnl = pricing.pnl_inr(p["side"], p["entry_price"], price, p["quantity"], p["unit"], p["instrument"])
        except (PriceUnavailable, pricing.LotSizeUnknown) as e:
            out.append({"position_id": p["position_id"], "error": str(e)})
            continue
        snap = await _rows(db, "SELECT snapshot_json FROM paper_snapshots WHERE position_id=?", (p["position_id"],))
        unhedged = 0.0
        for h in (json.loads(snap[0]["snapshot_json"])["holdings"] if snap else []):
            try:
                unhedged += h["qty"] * (await asyncio.to_thread(pricing.latest_close, h["ticker"]) - h["entry_price"])
            except PriceUnavailable:
                continue
        ts = _now()
        await db.execute("INSERT OR REPLACE INTO paper_marks VALUES (?,?,?,?,?,?)",
                         (p["position_id"], ts, price, pnl, unhedged, unhedged + pnl))
        out.append({"position_id": p["position_id"], "ts": ts, "mark_price": price, "pnl_inr": pnl,
                    "portfolio_pnl_unhedged_inr": unhedged, "portfolio_pnl_hedged_inr": unhedged + pnl})
    await db.commit()
    return out


@router.post("/mark")
async def mark():
    db = await connect()
    try:
        return {"marks": await mark_positions(db)}
    finally:
        await db.close()


@router.get("/positions")
async def positions(status: str = "open"):
    db = await connect()
    try:
        res = []
        for p in await _rows(db, "SELECT * FROM paper_positions WHERE status=?", (status,)):
            m = await _rows(db, "SELECT * FROM paper_marks WHERE position_id=? ORDER BY ts DESC LIMIT 1", (p["position_id"],))
            m = m[0] if m else {}
            res.append({**p, "last_mark": m.get("mark_price"), "pnl_inr": m.get("pnl_inr"),
                        "portfolio_pnl_unhedged_inr": m.get("portfolio_pnl_unhedged_inr"),
                        "portfolio_pnl_hedged_inr": m.get("portfolio_pnl_hedged_inr"), "marked_at": m.get("ts"),
                        "realised_pnl_inr": p.get("realised_pnl_inr"),
                        "price_label": {"spot_proxy": "spot proxy", "model_price": "model price"}.get(p["price_kind"], "close")})
        return res
    finally:
        await db.close()


@router.post("/close")
async def close(body: CloseIn):
    db = await connect()
    try:
        rows = await _rows(db, "SELECT * FROM paper_positions WHERE position_id=?", (body.position_id,))
        if not rows:
            raise HTTPException(404, "position not found")
        if rows[0]["status"] == "closed":
            raise HTTPException(409, "already closed")
        prop = await _rows(db, "SELECT hedge_json FROM paper_proposals WHERE proposal_id=?", (rows[0]["proposal_id"],))
        p = rows[0]
        try:
            price, _ = await asyncio.to_thread(pricing.mark_price, json.loads(prop[0]["hedge_json"]))
            realised = pricing.pnl_inr(p["side"], p["entry_price"], price, p["quantity"], p["unit"], p["instrument"])
        except (PriceUnavailable, pricing.LotSizeUnknown) as e:
            raise HTTPException(503, f"no price to close at: {e}")
        await db.execute("UPDATE paper_positions SET status='closed', exit_price=?, exit_ts=?, realised_pnl_inr=? "
                         "WHERE position_id=?", (price, _now(), round(realised, 2), body.position_id))
        await db.commit()
        return (await _rows(db, "SELECT * FROM paper_positions WHERE position_id=?", (body.position_id,)))[0]
    finally:
        await db.close()


@router.get("/history")
async def history(run_id: Optional[str] = None):
    db = await connect()
    try:
        w, a = ("WHERE run_id=?", (run_id,)) if run_id else ("", ())
        props = await _rows(db, f"SELECT * FROM paper_proposals {w}", a)
        ids = [p["proposal_id"] for p in props]
        q = ",".join("?" * len(ids)) or "''"
        poss = await _rows(db, f"SELECT * FROM paper_positions WHERE proposal_id IN ({q})", ids)
        pq = ",".join("?" * len(poss)) or "''"
        marks = await _rows(db, f"SELECT * FROM paper_marks WHERE position_id IN ({pq}) ORDER BY ts",
                            [p["position_id"] for p in poss])
        return {"proposals": props, "positions": poss, "marks": marks}
    finally:
        await db.close()
