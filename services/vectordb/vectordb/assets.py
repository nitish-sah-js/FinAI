"""Asset metadata: factor assets, benchmark rule (08 §5.4), price proxies and proxy groups (08 §6.6)."""
from __future__ import annotations

# Factor assets: quant's scenario_builder reads distribution entries for these ONLY when measure == "raw".
FACTOR_ASSETS = frozenset({"^NSEI", "CL=F", "BZ=F", "INR=X"})

# Yahoo has no history for the NSE sector indices (^CNXFMCG, ^CNXENERGY, ^CNXAUTO; checked 2026-10-03, also
# NIFTY_FMCG.NS / ^CNXAUTO.NS). Their outcomes are computed from the largest liquid constituent and the proxy is
# recorded in the outcome ("price_symbol", "proxy_note"). HINDUNILVR.NS for FMCG matches backtest/data/events.json.
PRICE_PROXIES: dict[str, str] = {
    "^CNXFMCG": "HINDUNILVR.NS",
    "^CNXENERGY": "RELIANCE.NS",
    "^CNXAUTO": "MARUTI.NS",
}

# Proxy groups (08 §6.6): when an analog lacks the exact requested ticker, an outcome of the same group stands in.
PROXY_GROUPS: dict[str, frozenset[str]] = {
    "crude": frozenset({"CL=F", "BZ=F"}),
    "us_refiners": frozenset({"VLO", "XLE", "RB=F"}),
    "us_insurers": frozenset({"ALL", "TRV"}),
    "in_fmcg": frozenset({"^CNXFMCG", "HINDUNILVR.NS", "ITC.NS", "NESTLEIND.NS", "BRITANNIA.NS", "DABUR.NS"}),
    "in_auto": frozenset({"^CNXAUTO", "MARUTI.NS", "M&M.NS", "ASHOKLEY.NS", "TVSMOTOR.NS", "TATAMOTORS.NS",
                          "BAJAJ-AUTO.NS", "HEROMOTOCO.NS", "EICHERMOT.NS"}),
    "in_banks": frozenset({"^NSEBANK", "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "KOTAKBANK.NS", "AXISBANK.NS"}),
    "in_upstream": frozenset({"ONGC.NS", "OIL.NS"}),
    "in_omc": frozenset({"BPCL.NS", "IOC.NS", "HINDPETRO.NS"}),
    "in_energy": frozenset({"^CNXENERGY", "RELIANCE.NS"}),
    "in_power": frozenset({"NTPC.NS", "TATAPOWER.NS", "POWERGRID.NS", "ADANIPOWER.NS", "COALINDIA.NS"}),
    "in_agri_inputs": frozenset({"UPL.NS", "CHAMBLFERT.NS", "COROMANDEL.NS", "PIIND.NS"}),
    "in_rice": frozenset({"KRBL.NS", "LTFOODS.NS"}),
    "in_ports": frozenset({"ADANIPORTS.NS"}),
}
_GROUP_OF = {t: g for g, members in PROXY_GROUPS.items() for t in members}


def group_of(ticker: str) -> str | None:
    return _GROUP_OF.get(ticker)


def is_indian(ticker: str) -> bool:
    return ticker.endswith(".NS") or ticker.startswith("^NSE") or ticker.startswith("^CNX") or ticker.endswith(".BO")


def benchmark_for(ticker: str) -> str:
    """08 §5.4: ^NSEI for Indian tickers and for INR=X, SPY otherwise."""
    if ticker == "INR=X" or is_indian(ticker):
        return "^NSEI"
    return "SPY"
