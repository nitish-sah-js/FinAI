"""Mark prices. MOCK=1 uses fixture closes. Labels: close | spot_proxy | model_price.
HedgeProposal (01 §5) has no strike/expiry fields, so options are parsed from `instrument`
(e.g. "RELIANCE 2900 PE", "NIFTY 25000 PE OCT").

Prices: copilot_common.prices (ingestion /prices, else yfinance directly). Options: Black–Scholes with
sigma = 1.1 × 20-day realised vol, from quant POST /bs_price (06) when it is up, else the same formula locally.
Never call a model price a market price.
"""
from __future__ import annotations

import calendar
import json
import os
import re
from datetime import date, timedelta
from math import erf, exp, log, sqrt

import httpx

from copilot_common import reachability
from copilot_common.prices import PriceUnavailable, get_closes_sync
from copilot_common.settings import get_settings

RISK_FREE = float(os.getenv("RISK_FREE", "0.065"))
MOCK_CLOSES = {"^NSEI": 25020.0, "^NSEBANK": 55000.0, "HDFCBANK.NS": 1000.0}
OPT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(CE|PE)\b", re.I)
MONTHS = [m.upper() for m in calendar.month_abbr[1:]]


class LotSizeUnknown(ValueError):
    pass


def _mock() -> bool:
    return os.environ["MOCK"] == "1" if "MOCK" in os.environ else get_settings().MOCK


def parse_instrument(instrument: str) -> dict:
    up = instrument.upper()
    m = OPT_RE.search(up)
    month = next((i + 1 for i, mo in enumerate(MONTHS) if re.search(rf"\b{mo}\b", up)), None)
    if m:
        return {"kind": "option", "strike": float(m.group(1)), "opt": "call" if m.group(2) == "CE" else "put", "month": month}
    return {"kind": "future" if "FUT" in up else "other", "month": month}


def approx_expiry(month: int | None, today: date | None = None) -> date:
    """Approximation: last day of the named month (next year if already past); 30 days out if no month given.
    Exact NSE expiry dates are not in the contract — replace with a real calendar if available."""
    today = today or date.today()
    if month is None:
        return today + timedelta(days=30)
    year = today.year if month >= today.month else today.year + 1
    return date(year, month, calendar.monthrange(year, month)[1])


def lot_size(symbol: str) -> int:
    """data/lot_sizes.json (verify against NSE). Unknown symbols raise: a silent default of 1 would make the P&L of a
    RELIANCE option hedge ~500× too small."""
    path = os.getenv("LOT_SIZES") or str(get_settings().data_dir / "lot_sizes.json")
    try:
        with open(path, encoding="utf-8") as f:
            table = json.load(f)
    except FileNotFoundError as e:
        raise LotSizeUnknown(f"{path} missing (data/lot_sizes.json, owned by 06)") from e
    key = symbol.upper().split()[0]
    if key not in table or key.startswith("_"):
        raise LotSizeUnknown(f"no lot size for {key} in {path}; add it (check nseindia.com)")
    v = table[key]
    return int(v["lot_size"] if isinstance(v, dict) else v)


def _closes(symbol: str, n: int = 25) -> list[float]:
    if _mock():
        px = MOCK_CLOSES.get(symbol, 100.0)
        return [px * (1 + 0.001 * ((i % 3) - 1)) for i in range(n - 1)] + [px]
    end = date.today()
    rows, _src = get_closes_sync(symbol, (end - timedelta(days=n * 2 + 10)).isoformat(), end.isoformat())
    closes = [c for _, c in rows][-n:]
    if not closes:
        raise PriceUnavailable(f"no closes for {symbol}")
    return closes


def latest_close(symbol: str) -> float:
    return _closes(symbol)[-1]


def realised_vol_20d(symbol: str) -> float:
    import numpy as np
    r = np.diff(np.log(np.asarray(_closes(symbol, 21))))
    return float(np.std(r, ddof=1) * np.sqrt(252))


def _bs_local(S, K, T, r, sigma, kind) -> float:
    N = lambda x: 0.5 * (1 + erf(x / sqrt(2)))  # noqa: E731
    d1 = (log(S / K) + (r + sigma ** 2 / 2) * T) / (sigma * sqrt(T))
    d2 = d1 - sigma * sqrt(T)
    return S * N(d1) - K * exp(-r * T) * N(d2) if kind == "call" else K * exp(-r * T) * N(-d2) - S * N(-d1)


def bs_price(S, K, T, r, sigma, kind) -> float:
    if _mock():
        return _bs_local(S, K, T, r, sigma, kind)
    url = get_settings().QUANT_URL.rstrip("/") + "/bs_price"
    if not reachability.is_down(url):
        try:
            resp = httpx.post(url, json={"S": S, "K": K, "T": T, "r": r, "sigma": sigma, "kind": kind},
                              timeout=httpx.Timeout(10, connect=reachability.CONNECT_TIMEOUT_S))
            resp.raise_for_status()
            j = resp.json()
            if "evidence" in j:  # tolerate ToolResult or flat {"price": ..}
                j = j["evidence"][0]["value"]
            return float(j["price"])
        except (httpx.ConnectError, httpx.ConnectTimeout):
            reachability.mark_down(url)
        except (httpx.HTTPError, ValueError, KeyError):
            pass
    return _bs_local(S, K, T, r, sigma, kind)        # same formula as 06; quant (L2) not reachable


def mark_price(hedge: dict) -> tuple[float, str]:
    """hedge is a HedgeProposal dict."""
    under, p = hedge["underlying"], parse_instrument(hedge["instrument"])
    if p["kind"] == "option":
        S = latest_close(under)
        T = max((approx_expiry(p["month"]) - date.today()).days, 1) / 365
        return bs_price(S, p["strike"], T, RISK_FREE, 1.1 * realised_vol_20d(under), p["opt"]), "model_price"
    if p["kind"] == "future" and under.startswith("^"):
        return latest_close(under), "spot_proxy"
    return latest_close(under), "close"


def pnl_inr(side: str, entry: float, mark: float, quantity: float, unit: str, instrument: str) -> float:
    sign = 1 if side.lower() == "buy" else -1
    if unit == "notional_inr":
        return sign * (mark / entry - 1) * quantity
    mult = lot_size(instrument) if unit == "lots" else 1
    return sign * (mark - entry) * mult * quantity
