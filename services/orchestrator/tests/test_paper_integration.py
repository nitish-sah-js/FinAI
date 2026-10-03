"""Paper trading wired into the real orchestrator app (12 Part B): a hedge from a stored FinalAnswer → position."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_DATA = Path(__file__).resolve().parents[3] / "data"
Q = "Cyclone heading to Odisha — what happens to my portfolio this week?"


@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setenv("LOT_SIZES", str(REPO_DATA / "lot_sizes.json"))   # the real, NSE-verified table
    from orchestrator.app import app
    with TestClient(app) as c:
        yield c


def _final(c) -> dict:
    acc = c.post("/query", json={"query": Q}).json()
    with c.websocket_connect(f"/ws/{acc['run_id']}") as ws:
        while True:
            m = ws.receive_json()
            if m["type"] == "final":
                return m["data"]


def test_end_to_end_propose_approve_mark_in_the_orchestrator(app_client):
    c = app_client
    final = _final(c)
    hedge = final["hedges"][0]
    p = c.post("/paper/propose", json={"run_id": final["run_id"], "hedge_id": hedge["hedge_id"]}).json()
    assert p["status"] == "pending" and json.loads(p["hedge_json"]) == hedge          # copied exactly
    r = c.post("/paper/approve", json={"proposal_id": p["proposal_id"], "decision": "approve", "approved_by": "Nitish"}).json()
    pos = r["position"]
    assert pos["quantity"] == hedge["quantity"] and pos["side"] == hedge["side"]       # never resized
    assert pos["price_kind"] == "spot_proxy"                                           # NIFTY FUT → ^NSEI spot proxy
    marks = c.post("/paper/mark", json={}).json()["marks"]
    assert marks and "error" not in marks[0]
    row = c.get("/paper/positions").json()[0]
    assert row["price_label"] == "spot proxy" and row["pnl_inr"] is not None
    hist = c.get("/paper/history", params={"run_id": final["run_id"]}).json()
    assert len(hist["proposals"]) == 1 and len(hist["positions"]) == 1 and hist["marks"]


def test_real_lot_sizes_are_the_2026_nse_values(monkeypatch):
    monkeypatch.setenv("LOT_SIZES", str(REPO_DATA / "lot_sizes.json"))
    from orchestrator.paper.pricing import LotSizeUnknown, lot_size
    assert lot_size("NIFTY OCT FUT short") == 65 and lot_size("BANKNIFTY") == 30
    with pytest.raises(LotSizeUnknown):          # no silent default of 1 (would shrink a stock-option P&L ~500×)
        lot_size("RELIANCE OCT 2800 PE")


def test_unknown_lot_size_keeps_proposal_pending(app_client):
    c = app_client
    final = _final(c)
    from orchestrator.paper.db import connect   # noqa: F401  (tables exist after the first request)
    bad = {**final["hedges"][0], "hedge_id": "hx", "instrument": "RELIANCE OCT 2800 PE", "underlying": "RELIANCE.NS"}
    import sqlite3
    from copilot_common.settings import get_settings
    con = sqlite3.connect(get_settings().data_dir / "ledger.db")
    stored = json.loads(con.execute("SELECT final_json FROM runs WHERE run_id=?", (final["run_id"],)).fetchone()[0])
    stored["hedges"].append(bad)
    con.execute("UPDATE runs SET final_json=? WHERE run_id=?", (json.dumps(stored), final["run_id"]))
    con.commit()
    con.close()
    p = c.post("/paper/propose", json={"run_id": final["run_id"], "hedge_id": "hx"}).json()
    r = c.post("/paper/approve", json={"proposal_id": p["proposal_id"], "decision": "approve", "approved_by": "N"})
    assert r.status_code == 422 and "RELIANCE" in r.json()["detail"]
    hist = c.get("/paper/history", params={"run_id": final["run_id"]}).json()
    assert hist["proposals"][0]["status"] == "pending" and hist["positions"] == []


def test_price_unavailable_returns_503_and_keeps_pending(app_client, monkeypatch):
    from copilot_common.prices import PriceUnavailable
    from orchestrator.paper import pricing

    def boom(hedge):
        raise PriceUnavailable("ingestion down and yfinance failed")
    monkeypatch.setattr(pricing, "mark_price", boom)
    c = app_client
    final = _final(c)
    p = c.post("/paper/propose", json={"run_id": final["run_id"], "hedge_id": final["hedges"][0]["hedge_id"]}).json()
    r = c.post("/paper/approve", json={"proposal_id": p["proposal_id"], "decision": "approve", "approved_by": "N"})
    assert r.status_code == 503
    assert c.get("/paper/history", params={"run_id": final["run_id"]}).json()["proposals"][0]["status"] == "pending"


def test_scheduler_is_started_with_the_app():
    from orchestrator import app as A
    assert A._paper_scheduler is not None
