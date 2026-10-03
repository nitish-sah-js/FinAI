"""Regenerate copilot_common/fixtures/*. Scenario: Cyclone 'Dana' approaching the Odisha coast.

Run:  python packages/copilot_common/make_fixtures.py
Numbers in the LLM fixtures (fixtures/llm/*.json) are taken from the tool fixtures, so the
Numbers-Ledger validator (04 §6) passes on a MOCK=1 run.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).parent / "copilot_common" / "fixtures"
AS_OF = "2026-10-03T02:30:00Z"
TS = "2026-10-03T08:45:00Z"


def ev(id_, tool, value, source, conf, summary, freshness=22500, url=None, model=None):
    return {"id": id_, "run_id": None, "tool": tool, "value": value, "summary": summary, "source": source,
            "source_url": url, "as_of": AS_OF, "timestamp": TS, "freshness_s": freshness, "confidence": conf,
            "degraded": False, "degraded_reason": None, "latency_ms": 420, "model_version": model}


def tr(*evs, warnings=None):
    return {"evidence": list(evs), "warnings": warnings or []}


NEWS_ITEMS = [
    {"news_id": "n1", "title": "IMD issues red alert as Cyclone Dana nears Odisha coast", "summary": "Landfall expected near Puri within 48 hours.",
     "source": "Economic Times", "url": "https://economictimes.indiatimes.com/", "published_at": "2026-10-03T05:10:00Z",
     "tickers": ["COALINDIA.NS", "NTPC.NS"], "category": "news"},
    {"news_id": "n2", "title": "Paradip port suspends operations ahead of cyclone", "summary": "Coal and crude handling halted.",
     "source": "Livemint", "url": "https://www.livemint.com/", "published_at": "2026-10-03T06:00:00Z",
     "tickers": ["COALINDIA.NS", "ADANIPORTS.NS"], "category": "news"},
    {"news_id": "n3", "title": "FMCG demand outlook steady despite weather disruptions", "summary": "Rural recovery intact, say analysts.",
     "source": "Business Standard", "url": "https://www.business-standard.com/", "published_at": "2026-10-03T04:30:00Z",
     "tickers": ["ITC.NS", "HINDUNILVR.NS"], "category": "news"},
]

TOOLS = {
    ("ingestion", "news"): tr(ev("ev_news_001", "news", {"items": NEWS_ITEMS}, "RSS (ET, Mint, BS) + GDELT", 0.9,
                                  "3 relevant headlines in the last 24h", freshness=9900)),
    ("ingestion", "weather_features"): tr(ev("ev_weather_001", "weather", {
        "region": "OD-Puri", "lat": 19.81, "lon": 85.83, "rain_anomaly_pct": 240, "heat_index_c": 31.5,
        "max_temp_c": 30.2, "soil_moisture_0_7cm": 0.41,
        "storm": {"name": "Dana", "category": "severe cyclonic storm", "basin": "Bay of Bengal", "track_toward": "Odisha coast"},
        "alerts": ["IMD red alert: Puri, Kendrapara, Jagatsinghpur"], "horizon_days": 5},
        "Open-Meteo forecast API + IMD bulletin", 0.62, "Severe cyclonic storm Dana tracking to Odisha; rain +240% vs normal",
        url="https://api.open-meteo.com/v1/forecast")),
    ("ingestion", "macro_features"): tr(ev("ev_macro_001", "macro", {
        "repo_rate": 5.5, "repo_change_bps": 0, "cpi_yoy": 3.1, "usd_inr": 88.4, "usd_inr_chg_5d": 0.004,
        "brent": 67.2, "brent_chg_5d": 0.021, "us10y": 4.12, "us10y_chg_5d": -0.03},
        "RBI, MoSPI, FRED, EIA (cached)", 0.85, "Brent +2.1% in 5d; repo unchanged", freshness=86400)),
    ("sentiment", "sentiment_score"): tr(ev("ev_sentiment_001", "sentiment", {
        "items": [
            {"news_id": "n1", "label": "negative", "score": -0.71, "confidence": 0.83, "model": "kdave/FineTuned_Finbert", "relevance": 0.9, "hinglish": False, "second_opinion": None, "weight": 0.31},
            {"news_id": "n2", "label": "negative", "score": -0.64, "confidence": 0.55, "model": "kdave/FineTuned_Finbert", "relevance": 0.8, "hinglish": False,
             "second_opinion": {"sentiment": "negative", "confidence": 0.74, "affected_tickers": ["COALINDIA.NS"], "materiality": "medium", "horizon": "days", "model": "gemma3:4b"}, "weight": 0.19},
            {"news_id": "n3", "label": "neutral", "score": 0.05, "confidence": 0.77, "model": "kdave/FineTuned_Finbert", "relevance": 0.5, "hinglish": False, "second_opinion": None, "weight": 0.21}],
        "portfolio_sentiment": -0.21, "by_ticker": {"COALINDIA.NS": -0.68, "NTPC.NS": -0.71, "ITC.NS": 0.05, "HINDUNILVR.NS": 0.05}},
        "FinBERT-India (kdave/FineTuned_Finbert) + gemma3:4b second opinion", 0.74, "Portfolio-weighted tone -0.21 (negative)",
        freshness=9900, model="finbert_india_v1")),
    ("agri", "agri_signal"): tr(ev("ev_agri_001", "agri", {
        "region_id": "OD-Cuttack", "district": "Cuttack", "state": "Odisha", "crop_season": "kharif", "main_crops": ["paddy"],
        "period_start": "2026-09-14", "period_end": "2026-09-29",
        "features": {"ndvi_mean": 0.52, "ndvi_anomaly_z": -0.6, "vci": 0.44, "rain_anomaly_pct": 18},
        "stress_class": "watch", "class_probs": {"healthy": 0.30, "watch": 0.45, "stressed": 0.20, "severe": 0.05},
        "yield_anomaly_pct": {"q10": -8.0, "q50": -3.5, "q90": 1.0}, "stress_lead_days": 30, "trend": "worsening",
        "linked_equities": ["ITC.NS", "UPL.NS"], "baseline_years": [2019, 2025], "n_years_baseline": 7,
        "confidence": 0.55, "model_version": "gbm_v1", "data_source": "Sentinel-2 NDVI (district zonal mean)",
        "label_source": "VCI + rainfall-deficit rules", "degraded": False},
        "gbm_v1 on Sentinel-2 NDVI", 0.55, "Cuttack kharif paddy: 'watch', NDVI -0.6σ", freshness=345600, model="gbm_v1")),
    ("vectordb", "find_analogs"): tr(ev("ev_analogs_001", "analogs", {
        "analogs": [
            {"event_id": "cyclone_fani_2019", "title": "Cyclone Fani landfall, Odisha", "event_date": "2019-05-03", "similarity": 0.87,
             "why_similar": "same coast, extremely severe cyclone, port and power disruption"},
            {"event_id": "cyclone_amphan_2020", "title": "Cyclone Amphan, West Bengal/Odisha", "event_date": "2020-05-20", "similarity": 0.81,
             "why_similar": "Bay of Bengal super cyclone, east-coast industrial belt"},
            {"event_id": "cyclone_phailin_2013", "title": "Cyclone Phailin, Odisha", "event_date": "2013-10-12", "similarity": 0.78,
             "why_similar": "Odisha landfall near Gopalpur, mass evacuation"},
            {"event_id": "cyclone_hudhud_2014", "title": "Cyclone Hudhud, Andhra Pradesh", "event_date": "2014-10-12", "similarity": 0.72,
             "why_similar": "east coast, port city hit (Visakhapatnam)"}],
        "distribution": [
            {"asset": "COALINDIA.NS", "horizon": "5d", "measure": "abnormal", "n": 4, "median": -0.012, "p10": -0.041, "p90": 0.012,
             "conformal_lo": -0.055, "conformal_hi": 0.02, "coverage_target": 0.8, "n_calib": 20},
            {"asset": "^NSEI", "horizon": "5d", "measure": "raw", "n": 4, "median": 0.002, "p10": -0.018, "p90": 0.015,
             "conformal_lo": -0.026, "conformal_hi": 0.022, "coverage_target": 0.8, "n_calib": 20}],
        "confidence": "medium", "filters_relaxed": False},
        "Weaviate HistoricalEvent (bge-small-en-v1.5, hybrid alpha 0.6)", 0.6, "4 analogs; COALINDIA 5d abnormal p10..p90 -4.1%..+1.2%",
        freshness=None)),
    ("quant", "exposure"): tr(ev("ev_exposure_001", "exposure", {
        "by_sector": {"Energy": 0.31, "Utilities": 0.12, "FMCG": 0.21, "Banks": 0.15, "Agri": 0.10, "Oil&Gas": 0.11},
        "by_ticker": [
            {"ticker": "COALINDIA.NS", "weight": 0.19, "beta": 0.71, "weather_sens": 0.6, "agri_sens": 0.0},
            {"ticker": "NTPC.NS", "weight": 0.12, "beta": 0.78, "weather_sens": 0.5, "agri_sens": 0.0},
            {"ticker": "ITC.NS", "weight": 0.13, "beta": 0.62, "weather_sens": 0.3, "agri_sens": 0.5},
            {"ticker": "HINDUNILVR.NS", "weight": 0.08, "beta": 0.55, "weather_sens": 0.2, "agri_sens": 0.4},
            {"ticker": "HDFCBANK.NS", "weight": 0.15, "beta": 1.02, "weather_sens": 0.0, "agri_sens": 0.1},
            {"ticker": "UPL.NS", "weight": 0.10, "beta": 0.95, "weather_sens": 0.4, "agri_sens": 0.8},
            {"ticker": "ONGC.NS", "weight": 0.11, "beta": 0.88, "weather_sens": 0.3, "agri_sens": 0.0},
            {"ticker": "RELIANCE.NS", "weight": 0.12, "beta": 1.05, "weather_sens": 0.2, "agri_sens": 0.0}],
        "east_coast_weight": 0.31,
        "heatmap": {"rows": ["Energy", "Utilities", "FMCG", "Banks", "Agri", "Oil&Gas"],
                    "cols": ["weather", "agri", "crude", "rates", "usd_inr"],
                    "values": [[0.6, 0.0, 0.3, -0.1, 0.1], [0.5, 0.0, 0.1, -0.3, 0.0], [0.3, 0.5, -0.2, -0.1, -0.1],
                               [0.0, 0.1, -0.1, 0.6, -0.2], [0.4, 0.8, -0.1, 0.0, 0.1], [0.3, 0.0, 0.8, -0.1, 0.3]],
                    "row_weights": {"Energy": 0.31, "Utilities": 0.12, "FMCG": 0.21, "Banks": 0.15, "Agri": 0.10, "Oil&Gas": 0.11}}},
        "quant engine (yfinance 500d, ^NSEI beta)", 0.8, "COALINDIA+NTPC = 31% of portfolio, high weather sensitivity", freshness=40000)),
    ("quant", "var_montecarlo"): tr(ev("ev_risk_001", "risk", {
        "method": "monte_carlo", "horizon_days": 5, "confidence_level": 0.95, "var_inr": 48200, "cvar_inr": 63900,
        "var_pct": 0.032, "paths": 10000, "portfolio_value_inr": 1506000,
        "pnl_hist": {"bin_edges": [-90000, -60000, -30000, 0, 30000, 60000, 90000], "counts": [180, 820, 3100, 3600, 1900, 400]}},
        "quant engine (Monte Carlo, 10,000 paths, t-copula)", 0.8, "5d 95% VaR ₹48,200 (3.2%)", freshness=40000)),
    ("quant", "hedge_proposals"): tr(ev("ev_hedge_001", "hedge", {
        "proposals": [{"hedge_id": "h1", "instrument": "NIFTY OCT FUT short", "underlying": "^NSEI", "side": "sell", "quantity": 1,
                       "unit": "lots", "hedge_ratio": 0.42, "est_cost_inr": 1850, "rationale": "beta hedge of energy/utility sleeve",
                       "sizing_method": "beta", "evidence_ids": ["ev_hedge_001"]}],
        "pre_var_inr": 48200, "post_var_inr": 29100, "portfolio_beta": 0.82},
        "quant engine (beta sizing, NSE lot sizes)", 0.75, "1 lot NIFTY short cuts VaR to ₹29,100", freshness=40000)),
    ("quant", "scenario"): tr(ev("ev_scenario_001", "scenario", {
        "shocks": {"crude": 10}, "pnl_inr": -6120.0, "pnl_pct": -0.0041,
        "by_ticker": {"RELIANCE.NS": -2900.0, "ONGC.NS": 4100.0, "ITC.NS": -1500.0, "HINDUNILVR.NS": -1200.0, "UPL.NS": -4620.0}},
        "quant engine (factor shocks)", 0.7, "crude +10% → ₹-6,120", freshness=40000)),
    ("quant", "event_study"): tr(ev("ev_event_study_001", "event_study", {
        "ticker": "COALINDIA.NS", "event_date": "2019-05-03", "window": [-1, 5], "car": -0.027, "car_t": -1.9, "p_value": 0.07,
        "ar_series": [0.001, -0.008, -0.011, -0.004, -0.003, -0.002, 0.0]},
        "quant engine (market model vs ^NSEI)", 0.7, "CAR -2.7% around Fani", freshness=None)),
}

LLM = {
    "intent": {"text": "", "parsed": {"intent": "event_impact", "event_type": "cyclone", "region": "Odisha", "tickers": [],
                                      "asset_classes": ["equity"], "horizon_days": 5, "references_portfolio": True,
                                      "needs_tools": ["weather", "analogs", "exposure", "risk", "sentiment"]}},
    "planner": {"text": "SUFFICIENT", "parsed": None},
    "sentiment2": {"text": "", "parsed": {"sentiment": "negative", "confidence": 0.78, "affected_tickers": ["ADANIPORTS.NS"],
                                          "materiality": "medium", "horizon": "days"}},
    "narrator_weather_agent": {"text": "", "parsed": {"agent": "weather_agent", "signal": "bearish",
        "summary": "Severe cyclonic storm Dana is tracking toward the Odisha coast with rainfall 240% above normal [ev_weather_001]. Coal and power names on the east coast are exposed. Weather confidence is moderate at 0.62 [ev_weather_001].",
        "evidence_ids": ["ev_weather_001"], "confidence": 0.62}},
    "narrator_agri_agent": {"text": "", "parsed": {"agent": "agri_agent", "signal": "bearish",
        "summary": "Cuttack kharif paddy is on 'watch' with NDVI -0.6σ below normal [ev_agri_001]; yield anomaly ranges from -8% to +1% (median -3.5%) [ev_agri_001]. This is a slow signal over weeks, not days.",
        "evidence_ids": ["ev_agri_001"], "confidence": 0.55}},
    "narrator_macro_agent": {"text": "", "parsed": {"agent": "macro_agent", "signal": "neutral",
        "summary": "Brent rose 2.1% over 5 days [ev_macro_001] while the repo rate is unchanged at 5.5% [ev_macro_001]. Macro is not the main driver this week.",
        "evidence_ids": ["ev_macro_001"], "confidence": 0.85}},
    "narrator_analog_agent": {"text": "", "parsed": {"agent": "analog_agent", "signal": "bearish",
        "summary": "Fani is the closest match (similarity 0.87) [ev_analogs_001]: same coast and port disruption, but it struck in May. Across 4 analogs, COALINDIA.NS 5-day abnormal returns ranged from -4.1% to +1.2% [ev_analogs_001]; the sample is small.",
        "evidence_ids": ["ev_analogs_001"], "confidence": 0.6}},
    "synthesizer": {"text": (
        "### Bottom line\n"
        "Severe cyclonic storm Dana is heading for the Odisha coast with rainfall 240% above normal [ev_weather_001]. "
        "Your most exposed holdings are Coal India and NTPC, 31% of the portfolio [ev_exposure_001].\n\n"
        "### Impact on your holdings\n"
        "- **COALINDIA.NS**: past cyclones moved it between -4.1% and +1.2% over 5 days [ev_analogs_001]; port shutdowns hit dispatches.\n"
        "- **NTPC.NS**: negative tone in recent news (-0.71) [ev_sentiment_001]; plant and grid disruption risk.\n"
        "- **Portfolio**: 5-day 95% VaR is ₹48,200 [ev_risk_001].\n\n"
        "### Suggested hedges\n"
        "- Sell 1 lot NIFTY OCT FUT (hedge ratio 0.42, est. cost ₹1,850) [ev_hedge_001], which cuts VaR to ₹29,100 [ev_hedge_001].\n\n"
        "### Confidence and what could be wrong\n"
        "Confidence: **medium**.\n"
        "- Only 4 analogs matched [ev_analogs_001].\n"
        "- Weather data is several hours old [ev_weather_001].\n\n"
        "_Decision support, not a trading signal._\n"
        "<json>{\"confidence\":\"medium\",\"holdings_impact\":[{\"ticker\":\"COALINDIA.NS\",\"impact\":\"negative\",\"range\":\"-4.1% to +1.2% (5d)\",\"evidence_ids\":[\"ev_analogs_001\",\"ev_exposure_001\"]},"
        "{\"ticker\":\"NTPC.NS\",\"impact\":\"negative\",\"range\":\"n/a\",\"evidence_ids\":[\"ev_sentiment_001\",\"ev_exposure_001\"]}],"
        "\"what_could_be_wrong\":[\"Only 4 analogs matched [ev_analogs_001]\",\"Weather data is several hours old [ev_weather_001]\"]}</json>"),
        "parsed": None},
    "red_team": {"text": "", "parsed": {"reasons": [
        "Only 4 analogs, and the p10-p90 range spans zero [ev_analogs_001]",
        "Weather evidence is several hours old; the track can shift [ev_weather_001]",
        "The beta hedge assumes beta 0.82 holds in stress; correlations rose in past cyclones [ev_hedge_001]"],
        "verdict": "proceed with caution", "verdict_reason": "Direction is plausible but the magnitude is poorly constrained."}},
    "explain": {"text": "1. parse_intent read the query as an event_impact question about a cyclone in Odisha.\n"
                        "2. The weather, sentiment, analog and exposure agents ran in parallel [ev_weather_001] [ev_sentiment_001] [ev_analogs_001] [ev_exposure_001].\n"
                        "3. The quant agent computed VaR and a beta hedge [ev_risk_001] [ev_hedge_001].\n"
                        "4. The synthesizer combined these; the red team and validator then checked the answer.", "parsed": None},
    "alert": {"text": "Cyclone Dana nearing Odisha; Coal India and NTPC exposed (31% of portfolio); confidence medium.", "parsed": None},
}


def main() -> None:
    for (svc, ep), data in TOOLS.items():
        p = OUT / svc / f"{ep}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    for role, data in LLM.items():
        p = OUT / "llm" / f"{role}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(TOOLS)} tool fixtures and {len(LLM)} llm fixtures to {OUT}")


if __name__ == "__main__":
    main()
