import json, os, sqlite3
from datetime import datetime, timezone
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.paper.db import SCHEMA
from orchestrator.paper.pricing import lot_size, parse_instrument, pnl_inr
from orchestrator.paper.router import router
from orchestrator.paper.scheduler import IST, should_mark

_H = {"hedge_ratio": 0.5, "rationale": "r", "sizing_method": "beta", "evidence_ids": ["ev_hedge_001"]}
HEDGES = [
    {"hedge_id": "h1", "instrument": "NIFTY OCT FUT short", "underlying": "^NSEI", "side": "sell", "quantity": 2, "unit": "lots", **_H},
    {"hedge_id": "h2", "instrument": "NIFTY 25000 PE", "underlying": "^NSEI", "side": "buy", "quantity": 1, "unit": "lots", **_H},
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "ledger.db"
    monkeypatch.setenv("LEDGER_DB", str(db))
    monkeypatch.setenv("MOCK", "1")
    ls = tmp_path / "lots.json"
    ls.write_text('{"NIFTY": 75}')
    monkeypatch.setenv("LOT_SIZES", str(ls))
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    con.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, final_json TEXT)")
    final = {"hedges": HEDGES, }
    con.execute("INSERT INTO runs VALUES (?,?)", ("run1", json.dumps(final)))
    con.commit(); con.close()
    app = FastAPI(); app.include_router(router, prefix="/paper")
    return TestClient(app)


def _propose(c, hid="h1"):
    return c.post("/paper/propose", json={"run_id": "run1", "hedge_id": hid})


def test_propose_approve_creates_position_with_same_quantity(client):
    p = _propose(client).json()
    assert p["status"] == "pending"
    assert json.loads(p["hedge_json"])["quantity"] == 2
    r = client.post("/paper/approve", json={"proposal_id": p["proposal_id"], "decision": "approve", "approved_by": "Nitish"})
    assert r.status_code == 200
    pos = r.json()["position"]
    assert pos["quantity"] == 2 and pos["price_kind"] == "spot_proxy" and pos["side"] == "sell"
    assert len(client.get("/paper/positions").json()) == 1


def test_reject_creates_no_position(client):
    p = _propose(client).json()
    r = client.post("/paper/approve", json={"proposal_id": p["proposal_id"], "decision": "reject", "approved_by": "Nitish"})
    assert r.json()["position"] is None and r.json()["proposal"]["status"] == "rejected"
    assert client.get("/paper/positions").json() == []


def test_unknown_hedge_404(client):
    assert _propose(client, "nope").status_code == 404


def test_approve_requires_approved_by(client):
    p = _propose(client).json()
    assert client.post("/paper/approve", json={"proposal_id": p["proposal_id"], "decision": "approve"}).status_code == 422
    assert client.post("/paper/approve", json={"proposal_id": p["proposal_id"], "decision": "approve", "approved_by": " "}).status_code == 422


def test_short_futures_pnl_sign(tmp_path, monkeypatch):
    f = tmp_path / 'lots.json'; f.write_text('{"NIFTY": 75}'); monkeypatch.setenv('LOT_SIZES', str(f))
    assert pnl_inr("sell", 25210.5, 25020.0, 1, "lots", "NIFTY OCT FUT short") == pytest.approx(190.5 * 75)
    assert pnl_inr("buy", 100, 90, 1, "lots", "NIFTY X") == pytest.approx(-10 * 75)
    assert lot_size("NIFTY") == 75


def test_option_uses_model_price_and_marks(client):
    p = _propose(client, "h2").json()
    pos = client.post("/paper/approve", json={"proposal_id": p["proposal_id"], "decision": "approve", "approved_by": "N"}).json()["position"]
    assert pos["price_kind"] == "model_price"
    assert client.post("/paper/mark", json={}).status_code == 200
    row = client.get("/paper/positions").json()[0]
    assert row["price_label"] == "model price" and row["marked_at"] is not None
    assert client.get("/paper/history", params={"run_id": "run1"}).json()["marks"]
    assert client.post("/paper/close", json={"position_id": pos["position_id"]}).json()["status"] == "closed"


def test_scheduler_windows():
    d = lambda h, m, day=5: datetime(2026, 10, day, h, m, tzinfo=IST)  # Mon 2026-10-05
    assert should_mark(d(9, 15)) and should_mark(d(9, 30)) and should_mark(d(15, 30)) and should_mark(d(15, 35))
    assert not should_mark(d(9, 20)) and not should_mark(d(8, 0)) and not should_mark(d(16, 0))
    assert not should_mark(d(10, 0, day=3))  # Saturday


def test_parse_instrument_and_notional():
    assert parse_instrument("RELIANCE 2900 PE")["kind"] == "option"
    assert parse_instrument("RELIANCE 2900 PE")["opt"] == "put" and parse_instrument("RELIANCE 2900 PE")["strike"] == 2900
    assert parse_instrument("NIFTY OCT FUT short") == {"kind": "future", "month": 10}
    assert pnl_inr("sell", 100.0, 90.0, 50000, "notional_inr", "X") == pytest.approx(5000.0)


def test_no_marks_on_nse_holidays():
    from orchestrator.paper.calendar import trading_day
    gandhi = datetime(2026, 10, 2, 10, 0, tzinfo=IST)          # Friday, Gandhi Jayanti: exchange closed
    assert not should_mark(gandhi)
    assert trading_day(gandhi.date()) == (False, "XBOM")
    assert trading_day(datetime(2026, 10, 5).date()) == (True, "XBOM")
    ok, src = trading_day(datetime(2031, 3, 4).date())            # past the published calendar: not guessed
    assert ok is True and src.startswith("weekday-only")


def test_close_records_realised_pnl(client):
    p = _propose(client).json()
    pos = client.post("/paper/approve", json={"proposal_id": p["proposal_id"], "decision": "approve", "approved_by": "N"}).json()["position"]
    closed = client.post("/paper/close", json={"position_id": pos["position_id"]}).json()
    expected = pnl_inr(pos["side"], pos["entry_price"], closed["exit_price"], pos["quantity"], pos["unit"], pos["instrument"])
    assert closed["realised_pnl_inr"] == pytest.approx(round(expected, 2))
    assert client.get("/paper/positions", params={"status": "closed"}).json()[0]["realised_pnl_inr"] == closed["realised_pnl_inr"]
    assert client.post("/paper/close", json={"position_id": pos["position_id"]}).status_code == 409


def test_new_position_is_marked_at_entry_right_away(client):
    p = _propose(client).json()
    client.post("/paper/approve", json={"proposal_id": p["proposal_id"], "decision": "approve", "approved_by": "N"})
    row = client.get("/paper/positions").json()[0]
    assert row["last_mark"] == row["entry_price"] and row["pnl_inr"] == 0.0 and row["marked_at"] is not None


def test_same_hedge_is_not_opened_twice(client):
    p = _propose(client).json()
    pos = client.post("/paper/approve", json={"proposal_id": p["proposal_id"], "decision": "approve", "approved_by": "N"}).json()["position"]
    again = _propose(client)
    assert again.status_code == 409 and again.json()["detail"]["position_id"] == pos["position_id"]
    assert len(client.get("/paper/positions").json()) == 1
    client.post("/paper/close", json={"position_id": pos["position_id"]})
    assert _propose(client).status_code == 200          # closed: it can be opened again


def test_realised_pnl_backfilled_for_old_closes(client):
    db = os.environ["LEDGER_DB"]
    con = sqlite3.connect(db)
    con.execute("INSERT INTO paper_positions (position_id, proposal_id, instrument, underlying, side, quantity, unit, "
                "entry_price, entry_ts, price_kind, status, exit_price, exit_ts) VALUES "
                "('pos_old','pp_x','NIFTY OCT FUT short','^NSEI','sell',1,'lots',100.0,'t','spot_proxy','closed',90.0,'t2')")
    con.commit(); con.close()
    row = client.get("/paper/positions", params={"status": "closed"}).json()[0]
    assert row["realised_pnl_inr"] == pytest.approx(10 * 75)        # short: entry 100, exit 90, 75 per lot


def test_market_status(client):
    m = client.get("/paper/market").json()
    assert set(m) >= {"open_now", "trading_day", "reason", "calendar", "now_ist"}
    assert (m["reason"] is None) == m["trading_day"]
