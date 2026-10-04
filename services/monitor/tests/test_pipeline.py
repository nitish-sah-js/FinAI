"""Stats from raw buffers, cooldown/escalation/merge, writer fallback, MOCK feed replay, store."""
import asyncio
from datetime import timedelta

import numpy as np
import pandas as pd

from copilot_common.service_base import now_utc
from monitor import stats, store
from monitor.dedupe import CooldownManager
from monitor.detectors import Candidate
from monitor.writer import post_check, template, write_headline


def run(c):
    return asyncio.run(c)


def bars(days=21, spike=0.0, vol_mult=1.0, seed=0):
    rng = np.random.default_rng(seed)
    # sessions end at 10:00 UTC (NSE close is 10:00 UTC), so a 200-minute window never straddles UTC midnight;
    # anchoring on "now" made this test fail between 00:00 and 03:20 UTC
    now = pd.Timestamp(now_utc())
    end = now.floor("D") + pd.Timedelta(hours=10)
    if end > now:
        end -= pd.Timedelta(days=1)
    idx = []
    for d in range(days, -1, -1):
        day_end = end - pd.Timedelta(days=d)
        idx += list(pd.date_range(day_end - pd.Timedelta(minutes=5 * 40), day_end, freq="5min"))
    idx = pd.DatetimeIndex(idx)
    r = rng.normal(0, 0.002, len(idx))
    r[-1] = spike
    close = 100 * np.cumprod(1 + r)
    vol = rng.integers(9000, 11000, len(idx)).astype(float)
    vol[-6:] *= vol_mult
    return pd.DataFrame({"close": close, "volume": vol}, index=idx)


def test_price_and_volume_inputs_from_bars():
    calm = stats.price_inputs({"A": bars()})[0]
    assert abs(calm["sigma_5m"] - 0.002) < 0.0004 and calm["history_days"] == 21
    hot = stats.price_inputs({"A": bars(spike=0.012)})[0]
    assert hot["r_5m"] / hot["sigma_5m"] > 4
    v = stats.volume_inputs({"A": bars(vol_mult=4.0)})[0]
    assert (np.log(v["v_30m"]) - v["mean_log_v"]) / v["std_log_v"] > 3


def test_news_and_sentiment_inputs():
    now = now_utc()
    items = ([{"title": f"ITC {i}", "tickers": ["ITC.NS"], "published_at": (now - timedelta(minutes=5 * i)).isoformat(),
               "sentiment_label": "negative", "sentiment_confidence": 0.7} for i in range(1, 6)]
             + [{"title": "ITC old", "tickers": ["ITC.NS"], "published_at": (now - timedelta(hours=20)).isoformat(),
                 "sentiment_label": "positive", "sentiment_confidence": 0.8},
                {"title": "Cigarette tax hike", "tickers": [], "published_at": (now - timedelta(minutes=3)).isoformat()}])
    n = stats.news_inputs(items, {"ITC.NS"}, now, {"ITC.NS": ["cigarette"]})[0]
    assert n["k"] == 6 and n["tagged"] and abs(n["lambda_h"] - 1 / 168) < 1e-9
    s = stats.sentiment_inputs(items, {"ITC.NS"}, now)[0]
    assert s["mean_6h"] == -0.7 and s["mean_24h"] == 0.8 and s["n_6h"] == 5


def test_cooldown_update_escalate_merge_expiry():
    cm = CooldownManager({"weather_threshold": 360, "price_z": 30, "news_burst": 60}, 10)
    t0 = now_utc()
    assert cm.decide("weather_threshold:OD-Puri", "weather_threshold", ["ONGC.NS"], 2, t0) == ("new", None)
    cm.record("weather_threshold:OD-Puri", "al_1", "weather_threshold", 2, 0.4, ["ONGC.NS"], t0)
    assert cm.decide("weather_threshold:OD-Puri", "weather_threshold", ["ONGC.NS"], 2, t0) == ("update", "al_1")
    assert cm.decide("weather_threshold:OD-Puri", "weather_threshold", ["ONGC.NS"], 3, t0) == ("escalate", "al_1")
    cm.record("price_z:ITC.NS", "al_2", "price_z", 2, 0.5, ["ITC.NS"], t0)
    assert cm.decide("news_burst:ITC.NS", "news_burst", ["ITC.NS"], 1, t0 + timedelta(minutes=5)) == ("merge", "al_2")
    assert cm.decide("news_burst:ITC.NS", "news_burst", ["ITC.NS"], 1, t0 + timedelta(minutes=11))[0] == "new"
    assert cm.decide("weather_threshold:OD-Puri", "weather_threshold", ["ONGC.NS"], 2, t0 + timedelta(hours=7))[0] == "new"


def _weather(conf=0.75, sev=0.9):
    return Candidate(kind="weather_threshold", tickers=["ONGC.NS"], key="OD-Puri", severity=sev, relevance=1.0,
                     confidence=conf, facts={"region": "OD-Puri", "alerts": ["cyclone"], "links": {"ONGC.NS": 1.0}},
                     evidence_ids=["ev_weather_001"])


def test_same_weather_twice_one_broadcast_then_escalation(isolated):
    eng = isolated
    eng.holdings = [{"ticker": "ONGC.NS", "qty": 1}]
    eng.weights = {"ONGC.NS": 0.10}
    sent = []

    async def go():
        await store.init_db()
        from monitor.delivery import ws_hub
        orig = ws_hub.hub.broadcast

        async def spy(msg):
            sent.append(msg)
            return await orig(msg)
        ws_hub.hub.broadcast = spy
        try:
            a = await eng.handle([_weather(sev=0.6)])            # impact 0.6·(0.4+0.6·0.4)=0.384 → tier 2
            b = await eng.handle([_weather(sev=0.6)])
            c = await eng.handle([_weather(sev=1.0)])            # 0.64 → tier 3 → escalation
        finally:
            ws_hub.hub.broadcast = orig
        return a, b, c
    a, b, c = run(go())
    alerts_sent = [m for m in sent if m["type"] == "alert"]                  # pet_reaction messages ride along
    assert len(a) == 1 and a[0].tier == 2 and b == [] and len(alerts_sent) == 2
    assert [m["data"]["reaction"] for m in sent if m["type"] == "pet_reaction"] == ["alert", "alert"]
    assert c[0].alert_id == a[0].alert_id and c[0].tier == 3 and c[0].reason.startswith("Escalated:")
    stored = run(store.list_alerts())
    assert len(stored) == 1 and stored[0].tier == 3 and eng.counts["updated"] == 1


def test_low_impact_dropped(isolated):
    eng = isolated
    eng.holdings = [{"ticker": "ONGC.NS", "qty": 1}]
    eng.weights = {}
    weak = _weather(sev=0.1)
    run(store.init_db())
    assert run(eng.handle([weak])) == [] and eng.counts["dropped_low_impact"] == 1


def test_writer_template_when_llm_raises(monkeypatch):
    import copilot_llm

    async def boom(*a, **k):
        raise RuntimeError("llm down")
    monkeypatch.setattr(copilot_llm.llm, "chat", boom)
    c = Candidate(kind="news_burst", tickers=["ITC.NS"], key="ITC.NS", severity=0.5, relevance=1, confidence=0.6,
                  facts={"k": 5, "lambda": 0.9, "z": 4.22})
    text, src = run(write_headline(c, 0.06))
    assert src == "template" and text == "ITC.NS: news burst (5 headlines in the last hour vs about 0.9 normally); 6% of portfolio; confidence medium."


def test_post_check_rejects_invented_numbers_and_long_text():
    facts = {"k": 5, "lambda": 0.9, "portfolio_weight_pct": 6}
    assert post_check("ITC.NS saw 5 headlines in an hour versus 0.9 normally; 6% of book; confidence medium.", facts)
    assert not post_check("ITC.NS saw 12 headlines; confidence medium.", facts)
    assert not post_check(" ".join(["word"] * 25), facts)


def test_mock_llm_headline_fails_post_check_so_template_is_used(isolated):
    """MOCK LLM returns a fixed cyclone sentence with 31%: not in these facts → template (never wrong numbers)."""
    c = Candidate(kind="news_burst", tickers=["ITC.NS"], key="ITC.NS", severity=0.5, relevance=1, confidence=0.6,
                  facts={"k": 5, "lambda": 0.9, "z": 4.22})
    assert run(write_headline(c, 0.06)) == (template(c, 0.06), "template")


def test_mock_feed_replay_produces_itc_news_burst(isolated):
    eng = isolated

    async def go():
        await store.init_db()
        await eng.refresh_portfolio()
        return await eng.replay_feed()
    alerts = run(go())
    kinds = {(a.kind, a.tickers[0]) for a in alerts}
    assert ("news_burst", "ITC.NS") in kinds
    a = next(a for a in alerts if a.kind == "news_burst")
    assert "10.0.0.3:3000/run/new?q=" in a.deeplink and f"alert={a.alert_id}" in a.deeplink and "q=q=" not in a.deeplink


def test_synthetic_weather_alert_is_stamped_and_stays_in_app(monkeypatch):
    """DEMO_MODE storm -> the alert headline says SIMULATED and no email / Telegram is sent."""
    from monitor import engine as eng_mod
    from monitor.delivery import email, telegram
    from monitor.detectors import Candidate
    sent = []
    monkeypatch.setattr(telegram, "enqueue", lambda *a, **k: sent.append("telegram"))
    monkeypatch.setattr(email, "enabled", lambda: True)
    monkeypatch.setattr(email, "send_email", lambda *a, **k: sent.append("email"))
    from monitor import store
    run(store.init_db())
    e = eng_mod.Engine()
    c = Candidate(kind="weather_threshold", tickers=["ONGC.NS"], key="OD-Puri", severity=0.9, relevance=1.0,
                  confidence=0.9, facts={"region": "OD-Puri", "alerts": ["cyclone"], "synthetic": True})
    a = run(e.build_alert(c, impact=0.9, t=3, weight=0.5))
    assert a.headline.startswith("SIMULATED: ")
    run(e.deliver(a))
    assert sent == []
