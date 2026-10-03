"""Integration with the project contract (01) and regressions found while integrating (2026-10-03)."""
import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from copilot_common.models import ToolResult

ROOT = Path(__file__).resolve().parents[3]

ITEMS = [
    {"news_id": "n1", "title": "Reliance shares slip as crude rally squeezes <b>refining</b> margins",
     "published_at": "2026-10-03T05:10:00Z", "tickers": ["RELIANCE.NS"]},
    {"news_id": "n2", "title": "ITC ka FMCG business mazboot, lekin cigarette volume par dabav",
     "published_at": "2026-10-03T04:00:00Z", "tickers": ["ITC.NS"]},
    {"news_id": "n3", "title": "HDFC Bank Q2 profit rises 18%, beats estimates",
     "published_at": "2026-10-04T09:00:00Z", "tickers": ["HDFCBANK.NS"]},
]
# the orchestrator sends the shared Portfolio model: avg_price may be missing, extra fields present
PORTFOLIO = {"portfolio_id": "demo", "currency": "INR", "cash": 0,
             "holdings": [{"ticker": "RELIANCE.NS", "qty": 100, "avg_price": 2850, "sector": "Energy"},
                          {"ticker": "ITC.NS", "qty": 400, "avg_price": None, "sector": "FMCG"}]}


def _fb(texts):
    return [{"label": "negative", "score": -0.7, "confidence": 0.86,
             "probs": {"negative": 0.86, "neutral": 0.1, "positive": 0.04}} if "slip" in t else
            {"label": "neutral", "score": 0.05, "confidence": 0.52,
             "probs": {"negative": 0.2, "neutral": 0.52, "positive": 0.28}} for t in texts]


@pytest.fixture
def client():
    from sentiment.main import app
    ps = [patch("sentiment.main.startup", new_callable=AsyncMock),
          patch("sentiment.main.score_async", new_callable=AsyncMock, side_effect=_fb),
          patch("sentiment.main.search_tickers", new_callable=AsyncMock,
                side_effect=lambda text, explicit: [(t, 1.0) for t in explicit]),
          patch("sentiment.model.get_scorer", return_value=MagicMock(device="cpu"))]
    for p in ps:
        p.start()
    try:
        with TestClient(app) as c:
            yield c
    finally:
        for p in ps:
            p.stop()


def test_response_validates_against_the_shared_contract(client):
    with patch("sentiment.main.ask_gemma", new_callable=AsyncMock, return_value=None):
        body = client.post("/sentiment/score", json={"items": ITEMS[:2], "portfolio": PORTFOLIO, "run_id": "run_x"}).json()
    tr = ToolResult.model_validate(body)                       # what the orchestrator does with the response
    ev = tr.evidence[0]
    assert ev.tool == "sentiment" and re.fullmatch(r"ev_sentiment_\d{3}", ev.id)
    assert ev.source.startswith("FinBERT") and ev.as_of.isoformat().startswith("2026-10-03T05:10")
    assert ev.freshness_s is not None and ev.latency_ms is not None
    assert {i["label"] for i in ev.value["items"]} <= {"positive", "neutral", "negative"}
    # the low-confidence Hinglish item needed a second opinion, the LLM was down → FinBERT kept + warning (07 §7)
    assert any("second opinion unavailable" in w for w in tr.warnings)
    assert any("avg_price missing for ITC.NS" in w for w in tr.warnings)


def test_second_opinion_merge_and_model_recorded(client):
    so = {"sentiment": "negative", "confidence": 0.8, "affected_tickers": ["ITC.NS"], "materiality": "medium",
          "horizon": "weeks", "model": "gemma3:4b"}
    with patch("sentiment.main.ask_gemma", new_callable=AsyncMock, return_value=so) as ask:
        body = client.post("/sentiment/score", json={"items": ITEMS[:2], "run_id": "run_y"}).json()
    item = next(i for i in body["evidence"][0]["value"]["items"] if i["news_id"] == "n2")
    assert ask.await_count == 1                                  # only the Hinglish / low-confidence headline
    assert item["label"] == "negative" and item["second_opinion"]["model"] == "gemma3:4b"
    assert "gemma3:4b second opinion" in body["evidence"][0]["source"]


def test_time_machine_drops_headlines_after_as_of(client):
    body = client.post("/sentiment/score", json={"items": ITEMS, "as_of": "2026-10-03", "second_opinion": False}).json()
    ids = {i["news_id"] for i in body["evidence"][0]["value"]["items"]}
    assert ids == {"n1", "n2"} and any("after as_of" in w for w in body["warnings"])


def test_html_is_stripped_before_scoring(client):
    with patch("sentiment.main.score_async", new_callable=AsyncMock, side_effect=_fb) as sc:
        client.post("/sentiment/score", json={"items": ITEMS[:1], "second_opinion": False})
    assert "<b>" not in sc.await_args.args[0][0]


def test_p3_prompt_is_in_sync_with_the_orchestrator():
    mine = (ROOT / "services" / "sentiment" / "sentiment" / "prompts" / "P3.txt").read_text(encoding="utf-8").strip()
    theirs = (ROOT / "services" / "orchestrator" / "prompts" / "P3.txt").read_text(encoding="utf-8").strip()
    assert mine == theirs


def test_kdave_label_order_override():
    """kdave/FineTuned_Finbert's config.id2label is wrong (eval: accuracy 0.098 with it, 0.775 alphabetical)."""
    with patch("sentiment.model.AutoTokenizer"), patch("sentiment.model.AutoModelForSequenceClassification") as m:
        mm = MagicMock()
        mm.config.id2label = {0: "Neutral", 1: "Positive", 2: "Negative"}   # what the hub config says
        mm.to.return_value = mm
        m.from_pretrained.return_value = mm
        from sentiment.model import FinbertScorer
        with patch("torch.cuda.is_available", return_value=False):
            sc = FinbertScorer("kdave/FineTuned_Finbert")
        assert sc.id2label == {0: "negative", 1: "neutral", 2: "positive"} and sc.label_source == "override"
        assert m.from_pretrained.call_args.kwargs.get("subfolder") == "finbert"   # weights live in finbert/
        mm.to.assert_called_with("cpu")                                       # model moved to the device


def test_position_weights_handle_missing_avg_price():
    from sentiment.weighting import _position_weights
    w = _position_weights({"holdings": [{"ticker": "A.NS", "qty": 10, "avg_price": None},
                                        {"ticker": "B.NS", "qty": 10, "avg_price": 1.0}]})
    assert w == {"A.NS": 0.5, "B.NS": 0.5}


@pytest.fixture(scope="module")
def index():
    from sentiment import ticker_map
    return asyncio.run(ticker_map.get_index())               # builds from data/nse_symbols.json (offline cache)


@pytest.mark.parametrize("text, must, must_not", [
    ("Coal India output falls in September on heavy rain", {"COALINDIA.NS"}, {"RAIN.NS"}),
    ("IMD issues red alert as Cyclone Dana nears Odisha coast", set(), {"BLUECOAST.NS"}),
    ("Rain Industries shares jump on carbon demand", {"RAIN.NS"}, set()),
    ("ITC and HUL lead FMCG gains", {"ITC.NS", "HINDUNILVR.NS"}, set()),
    ("HDFC Bank Q2 profit rises 18%", {"HDFCBANK.NS"}, set()),
])
def test_ticker_linking_on_real_headlines(index, text, must, must_not):
    got = {t for t, _ in index.search(text, [])}
    assert must <= got, got
    assert not (must_not & got), got
