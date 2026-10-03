"""Build evals/fixtures/case_XX.json: one frozen evidence bundle per golden case (05 §4, step 3).

    python evals/make_fixtures.py

Each bundle = {case_id, query, lang, portfolio, evidence:[Evidence...]} with weather, agri, macro, analogs, exposure,
sentiment, risk and hedge evidence whose numbers fit that case (Gulf hurricane, Odisha cyclone, monsoon deficit, ...).
Frozen on purpose: P4–P9 are scored on identical inputs every run, so a score change means the prompt/model changed.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
AS_OF, TS = "2026-10-03T02:30:00Z", "2026-10-03T08:45:00Z"

PORTFOLIO = {"portfolio_id": "demo", "currency": "INR", "cash": 0.0, "holdings": [
    {"ticker": "RELIANCE.NS", "qty": 50, "avg_price": 2850, "sector": "Energy"},
    {"ticker": "ONGC.NS", "qty": 600, "avg_price": 255, "sector": "Oil&Gas"},
    {"ticker": "COALINDIA.NS", "qty": 700, "avg_price": 410, "sector": "Energy"},
    {"ticker": "NTPC.NS", "qty": 500, "avg_price": 355, "sector": "Utilities"},
    {"ticker": "ITC.NS", "qty": 450, "avg_price": 430, "sector": "FMCG"},
    {"ticker": "HINDUNILVR.NS", "qty": 50, "avg_price": 2450, "sector": "FMCG"},
    {"ticker": "HDFCBANK.NS", "qty": 120, "avg_price": 1850, "sector": "Banks"},
    {"ticker": "UPL.NS", "qty": 230, "avg_price": 650, "sector": "Agri"}]}

# one row per golden case: the numbers each tool "returned"
CASES = {
    1: dict(query="Hurricane in the Gulf heading to Louisiana — impact on my energy stocks this week?", lang="en",
            weather=dict(region="US-GulfCoast", lat=29.95, lon=-90.07, rain_anomaly_pct=310, heat_index_c=34.0, max_temp_c=31.5,
                         storm=dict(name="Delta", category="Category 4 hurricane", basin="Atlantic", track_toward="Louisiana refineries"),
                         alerts=["NHC hurricane warning: Louisiana coast"], conf=0.7),
            agri=dict(region_id="MH-Yavatmal", stress="healthy", z=0.3, q=(-2.0, 1.5, 4.0), conf=0.6),
            macro=dict(brent=71.8, brent_chg_5d=0.034, usd_inr=88.2, usd_inr_chg_5d=0.002, repo_change_bps=0),
            analogs=[("hurricane_ida_2021", "Hurricane Ida, Louisiana", 0.91), ("hurricane_laura_2020", "Hurricane Laura, Louisiana", 0.84),
                     ("hurricane_harvey_2017", "Hurricane Harvey, Texas", 0.79), ("hurricane_katrina_2005", "Hurricane Katrina", 0.74)],
            dist=[("NG=F", "raw", 0.052, -0.010, 0.110), ("RELIANCE.NS", "abnormal", 0.008, -0.012, 0.031)],
            var=(5, 51800, 0.034), hedge=("NIFTY OCT FUT short", 1, 0.38, 1850, 33400)),
    2: dict(query="Cyclone heading to Odisha — what happens to my portfolio?", lang="en",
            weather=dict(region="OD-Puri", lat=19.81, lon=85.83, rain_anomaly_pct=240, heat_index_c=31.5, max_temp_c=30.2,
                         storm=dict(name="Dana", category="severe cyclonic storm", basin="Bay of Bengal", track_toward="Odisha coast"),
                         alerts=["IMD red alert: Puri, Kendrapara"], conf=0.62),
            agri=dict(region_id="OD-Cuttack", stress="watch", z=-0.6, q=(-8.0, -3.5, 1.0), conf=0.55),
            macro=dict(brent=67.2, brent_chg_5d=0.021, usd_inr=88.4, usd_inr_chg_5d=0.004, repo_change_bps=0),
            analogs=[("cyclone_fani_2019", "Cyclone Fani, Odisha", 0.87), ("cyclone_amphan_2020", "Cyclone Amphan", 0.81),
                     ("cyclone_phailin_2013", "Cyclone Phailin, Odisha", 0.78), ("cyclone_hudhud_2014", "Cyclone Hudhud", 0.72)],
            dist=[("COALINDIA.NS", "abnormal", -0.012, -0.041, 0.012), ("^NSEI", "raw", 0.002, -0.018, 0.015)],
            var=(5, 48200, 0.032), hedge=("NIFTY OCT FUT short", 1, 0.42, 1850, 29100)),
    3: dict(query="Agar monsoon kamzor raha toh FMCG ka kya hoga next month?", lang="hinglish",
            weather=dict(region="IN-All", lat=21.0, lon=78.0, rain_anomaly_pct=-24, heat_index_c=36.0, max_temp_c=35.1, storm=None,
                         alerts=["IMD: monsoon deficit in central India"], conf=0.66),
            agri=dict(region_id="MH-Yavatmal", stress="stressed", z=-1.4, q=(-18.0, -9.5, -1.0), conf=0.62),
            macro=dict(brent=69.0, brent_chg_5d=0.004, usd_inr=88.6, usd_inr_chg_5d=0.003, repo_change_bps=0),
            analogs=[("monsoon_deficit_2014", "2014 monsoon deficit", 0.86), ("monsoon_deficit_2009", "2009 drought", 0.82),
                     ("monsoon_deficit_2015", "2015 monsoon deficit", 0.80)],
            dist=[("^CNXFMCG", "raw", -0.021, -0.064, 0.018), ("HINDUNILVR.NS", "abnormal", -0.015, -0.052, 0.020)],
            var=(20, 96500, 0.064), hedge=("NIFTY OCT 24500 PE buy", 2, 0.30, 7400, 71200)),
    4: dict(query="What's my 1-day VaR?", lang="en",
            weather=dict(region="IN-All", lat=21.0, lon=78.0, rain_anomaly_pct=4, heat_index_c=33.0, max_temp_c=32.0, storm=None, alerts=[], conf=0.7),
            agri=dict(region_id="MH-Yavatmal", stress="healthy", z=0.2, q=(-2.5, 0.5, 3.0), conf=0.6),
            macro=dict(brent=68.4, brent_chg_5d=-0.006, usd_inr=88.3, usd_inr_chg_5d=-0.001, repo_change_bps=0),
            analogs=[("covid_crash_2020", "COVID crash, March 2020", 0.52), ("taper_tantrum_2013", "Taper tantrum 2013", 0.47)],
            dist=[("^NSEI", "raw", 0.001, -0.014, 0.012)],
            var=(1, 22100, 0.0147), hedge=("NIFTY OCT FUT short", 1, 0.35, 1850, 15900)),
    5: dict(query="Hedge my Reliance and ONGC position against an oil crash", lang="en",
            weather=dict(region="IN-All", lat=21.0, lon=78.0, rain_anomaly_pct=-3, heat_index_c=33.5, max_temp_c=32.4, storm=None, alerts=[], conf=0.7),
            agri=dict(region_id="MH-Yavatmal", stress="healthy", z=0.1, q=(-3.0, 0.0, 2.5), conf=0.6),
            macro=dict(brent=58.9, brent_chg_5d=-0.12, usd_inr=88.9, usd_inr_chg_5d=0.006, repo_change_bps=0),
            analogs=[("oil_crash_2020", "April 2020 oil crash", 0.88), ("opec_price_war_2014", "OPEC price war 2014", 0.83),
                     ("oil_crash_2008", "2008 oil collapse", 0.76)],
            dist=[("ONGC.NS", "abnormal", -0.046, -0.112, 0.008), ("RELIANCE.NS", "abnormal", -0.018, -0.061, 0.014)],
            var=(5, 57300, 0.038), hedge=("RELIANCE OCT 2800 PE buy", 1, 0.55, 9800, 31700)),
    6: dict(query="RBI hiked repo by 50bps, which of my banks get hurt?", lang="en",
            weather=dict(region="IN-All", lat=21.0, lon=78.0, rain_anomaly_pct=2, heat_index_c=33.0, max_temp_c=31.9, storm=None, alerts=[], conf=0.7),
            agri=dict(region_id="MH-Yavatmal", stress="healthy", z=0.0, q=(-2.0, 0.5, 2.8), conf=0.6),
            macro=dict(brent=68.0, brent_chg_5d=0.003, usd_inr=88.1, usd_inr_chg_5d=-0.004, repo_change_bps=50, repo_rate=6.0),
            analogs=[("rbi_offcycle_hike_2022", "RBI off-cycle hike, May 2022", 0.89), ("rbi_hike_2013", "RBI hike 2013", 0.77)],
            dist=[("HDFCBANK.NS", "abnormal", -0.019, -0.048, 0.006), ("^NSEBANK", "raw", -0.022, -0.051, 0.004)],
            var=(5, 44900, 0.030), hedge=("BANKNIFTY OCT FUT short", 1, 0.40, 2200, 30100)),
    7: dict(query="Heatwave in north India — power and cooling stocks?", lang="en",
            weather=dict(region="DL-NewDelhi", lat=28.61, lon=77.21, rain_anomaly_pct=-60, heat_index_c=49.0, max_temp_c=46.5, storm=None,
                         alerts=["IMD heatwave warning: Delhi, Punjab, Haryana"], conf=0.72),
            agri=dict(region_id="PB-Ludhiana", stress="watch", z=-0.8, q=(-7.0, -2.5, 1.5), conf=0.58),
            macro=dict(brent=69.5, brent_chg_5d=0.011, usd_inr=88.3, usd_inr_chg_5d=0.001, repo_change_bps=0),
            analogs=[("heatwave_2022", "North India heatwave 2022", 0.85), ("heatwave_2019", "Heatwave 2019", 0.78),
                     ("heatwave_2023", "Heatwave 2023", 0.74)],
            dist=[("NTPC.NS", "abnormal", 0.017, -0.006, 0.041), ("^NSEI", "raw", 0.001, -0.012, 0.013)],
            var=(5, 46100, 0.031), hedge=("NIFTY OCT FUT short", 1, 0.33, 1850, 34800)),
    8: dict(query="Explain your last recommendation", lang="en",
            weather=dict(region="OD-Puri", lat=19.81, lon=85.83, rain_anomaly_pct=240, heat_index_c=31.5, max_temp_c=30.2,
                         storm=dict(name="Dana", category="severe cyclonic storm", basin="Bay of Bengal", track_toward="Odisha coast"),
                         alerts=["IMD red alert: Puri"], conf=0.62),
            agri=dict(region_id="OD-Cuttack", stress="watch", z=-0.6, q=(-8.0, -3.5, 1.0), conf=0.55),
            macro=dict(brent=67.2, brent_chg_5d=0.021, usd_inr=88.4, usd_inr_chg_5d=0.004, repo_change_bps=0),
            analogs=[("cyclone_fani_2019", "Cyclone Fani, Odisha", 0.87), ("cyclone_amphan_2020", "Cyclone Amphan", 0.81)],
            dist=[("COALINDIA.NS", "abnormal", -0.012, -0.041, 0.012)],
            var=(5, 48200, 0.032), hedge=("NIFTY OCT FUT short", 1, 0.42, 1850, 29100)),
    9: dict(query="Summarise the market today", lang="en",
            weather=dict(region="IN-All", lat=21.0, lon=78.0, rain_anomaly_pct=6, heat_index_c=33.2, max_temp_c=32.1, storm=None, alerts=[], conf=0.7),
            agri=dict(region_id="MH-Yavatmal", stress="healthy", z=0.4, q=(-1.5, 1.0, 3.5), conf=0.6),
            macro=dict(brent=68.7, brent_chg_5d=0.009, usd_inr=88.2, usd_inr_chg_5d=-0.002, repo_change_bps=0),
            analogs=[("quiet_session_2024", "Range-bound session, 2024", 0.41)],
            dist=[("^NSEI", "raw", 0.002, -0.009, 0.011)],
            var=(1, 21400, 0.0142), hedge=("NIFTY OCT FUT short", 1, 0.30, 1850, 16200)),
    10: dict(query="ITC ke liye kharif crop stress kitna bura hai?", lang="hinglish",
             weather=dict(region="MP-Indore", lat=22.72, lon=75.86, rain_anomaly_pct=-31, heat_index_c=37.5, max_temp_c=36.2, storm=None,
                          alerts=["IMD: deficient rainfall, west Madhya Pradesh"], conf=0.64),
             agri=dict(region_id="MP-Indore", stress="stressed", z=-1.7, q=(-21.0, -11.0, -2.0), conf=0.6),
             macro=dict(brent=68.8, brent_chg_5d=0.002, usd_inr=88.5, usd_inr_chg_5d=0.002, repo_change_bps=0),
             analogs=[("soy_deficit_2015", "MP soy crop failure 2015", 0.84), ("monsoon_deficit_2014", "2014 monsoon deficit", 0.79),
                      ("kharif_stress_2018", "Kharif stress 2018", 0.71)],
             dist=[("ITC.NS", "abnormal", -0.011, -0.038, 0.009), ("UPL.NS", "abnormal", -0.024, -0.067, 0.012)],
             var=(5, 47700, 0.032), hedge=("NIFTY OCT FUT short", 1, 0.36, 1850, 33900)),
}


def ev(id_, tool, value, conf, summary, source, freshness=22500):
    return {"id": id_, "run_id": None, "tool": tool, "value": value, "summary": summary, "source": source, "source_url": None,
            "as_of": AS_OF, "timestamp": TS, "freshness_s": freshness, "confidence": conf, "degraded": False,
            "degraded_reason": None, "latency_ms": 400, "model_version": None, "staleness_factor": None}


def bundle(cid: int, c: dict) -> dict:
    w, a, m = c["weather"], c["agri"], c["macro"]
    weather = ev("ev_weather_001", "weather", {
        "region": w["region"], "lat": w["lat"], "lon": w["lon"], "rain_anomaly_pct": w["rain_anomaly_pct"],
        "heat_index_c": w["heat_index_c"], "max_temp_c": w["max_temp_c"], "soil_moisture_0_7cm": 0.33,
        "storm": w["storm"], "alerts": w["alerts"], "horizon_days": 5}, w["conf"],
        f"{w['region']}: rain {w['rain_anomaly_pct']:+d}% vs normal, max {w['max_temp_c']} °C", "Open-Meteo forecast + IMD/NHC")
    q10, q50, q90 = a["q"]
    agri = ev("ev_agri_001", "agri", {
        "region_id": a["region_id"], "crop_season": "kharif", "features": {"ndvi_anomaly_z": a["z"], "vci": round(0.5 + a["z"] / 5, 2)},
        "stress_class": a["stress"], "yield_anomaly_pct": {"q10": q10, "q50": q50, "q90": q90}, "stress_lead_days": 30,
        "model_version": "gbm_v1", "data_source": "Sentinel-2 NDVI (district zonal mean)"}, a["conf"],
        f"{a['region_id']}: {a['stress']}, NDVI z {a['z']:+.1f}", "gbm_v1 on Sentinel-2 NDVI", freshness=345600)
    macro = ev("ev_macro_001", "macro", {
        "repo_rate": m.get("repo_rate", 5.5), "repo_change_bps": m["repo_change_bps"], "cpi_yoy": 3.1,
        "usd_inr": m["usd_inr"], "usd_inr_chg_5d": m["usd_inr_chg_5d"], "brent": m["brent"], "brent_chg_5d": m["brent_chg_5d"],
        "us10y": 4.12, "us10y_chg_5d": -0.03}, 0.85,
        f"Brent {m['brent_chg_5d'] * 100:+.1f}% 5d; repo {m['repo_change_bps']:+d} bps", "RBI, MoSPI, FRED, EIA", freshness=86400)
    analogs = ev("ev_analogs_001", "analogs", {
        "analogs": [{"event_id": e, "title": t, "similarity": s} for e, t, s in c["analogs"]],
        "distribution": [{"asset": asset, "horizon": "20d" if c["var"][0] >= 20 else "5d", "measure": meas, "n": len(c["analogs"]),
                          "median": med, "p10": p10, "p90": p90} for asset, meas, med, p10, p90 in c["dist"]],
        "confidence": "high" if len(c["analogs"]) >= 4 else "medium" if len(c["analogs"]) >= 3 else "low"}, 0.6,
        f"{len(c['analogs'])} analogs", "Weaviate HistoricalEvent", freshness=None)
    exposure = ev("ev_exposure_001", "exposure", {
        "by_sector": {"Energy": 0.31, "Oil&Gas": 0.11, "Utilities": 0.12, "FMCG": 0.21, "Banks": 0.15, "Agri": 0.10}}, 0.8,
        "Energy 31%, FMCG 21%, Banks 15%", "quant engine", freshness=40000)
    sentiment = ev("ev_sentiment_001", "sentiment", {"portfolio_sentiment": -0.18, "by_ticker": {}}, 0.7,
                   "Portfolio-weighted tone -0.18", "FinBERT-India", freshness=9000)
    h, qty, ratio, cost, post = c["hedge"]
    horizon, var_inr, var_pct = c["var"]
    risk = ev("ev_risk_001", "risk", {"method": "monte_carlo", "horizon_days": horizon, "confidence_level": 0.95,
                                      "var_inr": var_inr, "cvar_inr": round(var_inr * 1.33), "var_pct": var_pct}, 0.8,
              f"{horizon}d 95% VaR ₹{var_inr:,}", "quant engine (Monte Carlo)", freshness=40000)
    hedge = ev("ev_hedge_001", "hedge", {"proposals": [{
        "hedge_id": "h1", "instrument": h, "underlying": "^NSEBANK" if "BANK" in h else "^NSEI",
        "side": "buy" if h.endswith(" buy") else "sell", "quantity": qty, "unit": "lots", "hedge_ratio": ratio,
        "est_cost_inr": cost, "rationale": "beta hedge", "sizing_method": "beta", "evidence_ids": ["ev_hedge_001"]}],
        "pre_var_inr": var_inr, "post_var_inr": post, "portfolio_beta": 0.82}, 0.75,
        f"{qty} lot {h}", "quant engine (beta sizing)", freshness=40000)
    return {"case_id": cid, "query": c["query"], "lang": c["lang"], "portfolio": PORTFOLIO,
            "evidence": [weather, agri, macro, analogs, exposure, sentiment, risk, hedge]}


def main() -> None:
    out = HERE / "fixtures"
    out.mkdir(exist_ok=True)
    for cid, c in CASES.items():
        (out / f"case_{cid:02d}.json").write_text(json.dumps(bundle(cid, c), indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(CASES)} bundles to {out}")


if __name__ == "__main__":
    main()
