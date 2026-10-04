"""News relevance (Phase 5, found by scripts/verify_usage.py): the orchestrator sends a whole sentence as the news
query. The old filter kept an RSS headline only if it contained EVERY word of that sentence, so live questions got
0 headlines and the sentiment service had nothing to score."""
from services.ingestion.handlers import query_terms, relevance

Q = "oil . A severe cyclone hits Odisha. Crude oil jumps 15% after a surprise OPEC cut. What happens to my portfolio?"


def test_query_terms_drop_filler():
    t = query_terms(Q)
    assert {"crude", "opec", "cyclone", "odisha", "jumps"} <= set(t)
    assert not {"what", "happens", "portfolio", "after"} & set(t)


def test_relevance_any_keyword_or_ticker():
    terms = query_terms(Q)
    opec = {"title": "OPEC+ agrees surprise output cut", "summary": "", "tickers": []}
    reliance = {"title": "Reliance Q2 preview", "summary": "", "tickers": ["RELIANCE.NS"]}
    ipo = {"title": "Tanvi Exports files for IPO", "summary": "gold jewellery maker", "tickers": []}
    assert relevance(opec, terms, set()) > 0
    assert relevance(reliance, terms, {"RELIANCE.NS"}) > relevance(opec, terms, {"RELIANCE.NS"})
    assert relevance(ipo, terms, {"RELIANCE.NS"}) == 0
