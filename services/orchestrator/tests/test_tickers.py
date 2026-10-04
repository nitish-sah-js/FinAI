"""Ticker hygiene in parse_intent: junk from the model is dropped, and intent tickers add to the portfolio."""
from copilot_common.models import Intent
from orchestrator.nodes.parse_intent import clean_ticker, named_in_query, normalise, scope_tickers

PF = {"holdings": [{"ticker": "RELIANCE.NS", "qty": 1}, {"ticker": "ITC.NS", "qty": 1}, {"ticker": "HDFCBANK.NS", "qty": 1}]}


def _state(query: str, tickers: list[str]) -> dict:
    return {"request": {"query": query}, "portfolio": PF, "intent": {"tickers": tickers}}


def test_clean_ticker_keeps_real_symbols_and_drops_junk():
    assert clean_ticker("HDFCBANK.NS") == "HDFCBANK.NS"
    assert clean_ticker("sbin") == "SBIN.NS"                       # bare symbol gets the NSE suffix
    assert clean_ticker("^NSEI") == "^NSEI"                        # indices / futures the tools use
    assert clean_ticker("NG=F") == "NG=F"
    assert clean_ticker("BOB.NS'],'ASSET_CLASSES':[") is None      # seen in a live run
    assert clean_ticker("ZZZZNOTREAL.NS") is None                  # well-formed but not a listed symbol
    assert clean_ticker("") is None and clean_ticker("hdfc bank") is None


def test_normalise_drops_junk_tickers():
    raw = Intent(intent="portfolio_risk", tickers=["HDFCBANK.NS", "BOB.NS'],'ASSET_CLASSES':[", "null"])
    out = normalise(raw, "RBI may hike the repo rate - how exposed are my bank holdings?")
    assert out.tickers == ["HDFCBANK.NS"]


def test_named_in_query_by_alias_and_symbol():
    assert "ITC.NS" in named_in_query("What happens to ITC if the monsoon fails?")
    assert "RELIANCE.NS" in named_in_query("and for reliance industries?")
    assert named_in_query("A cyclone in the Bay of Bengal: what happens to my FMCG stocks?") == []


def test_intent_tickers_add_to_portfolio_unless_user_named_stocks():
    # model inferred extra banks, user named none -> portfolio first, extras appended
    s = _state("how exposed are my bank holdings?", ["ICICIBANK.NS", "HDFCBANK.NS"])
    assert scope_tickers(s) == ["RELIANCE.NS", "ITC.NS", "HDFCBANK.NS", "ICICIBANK.NS"]
    # user named a stock -> that stock is the scope
    s = _state("What happens to ITC if crude rises?", ["ITC.NS", "CL=F"])
    assert scope_tickers(s) == ["ITC.NS"]
    # indices never become agent tickers
    s = _state("will the market fall?", ["^NSEI"])
    assert "^NSEI" not in scope_tickers(s)


def test_null_strings_become_none():
    raw = Intent(intent="market_summary", region="null", event_type=None)
    assert normalise(raw, "how is the market").region is None
