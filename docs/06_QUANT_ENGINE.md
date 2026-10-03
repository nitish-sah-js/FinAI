# 06 — Quant Engine Service (L2 :8101)

| | |
|---|---|
| **Owner / Laptop** | Quant person · **L2 "Quant & ML"** · port **8101** · folder `services/quant` |
| **Depends on** | `copilot_common` (01). Price data from ingestion `POST /prices` (10). The service falls back to direct `yfinance` plus the cache if ingestion is down. Uses `data/lot_sizes.json` and `data/sector_sensitivity.json` |
| **Provides** | Tools `event_study`, `correlations`, `risk`, `scenario`, `hedge`, `exposure`, `hedge_validation` (canonical names from 01 §4) |
| **Status** | ✅ Built and verified 2026-10-03 (`services/quant`, 42 tests). Includes the hedge-review fixes in §9 |
| **Called by** | Orchestrator `exposure_agent` (fan-out) and `quant_agent` (after join) (04). The what-if sliders in the terminal call `/scenario` through the orchestrator proxy (13). Backtest (12) |

> **Principle.** All numbers come from code (numpy, pandas, scipy). LLMs only narrate these numbers. Every endpoint returns `ToolResult` (01 §5), is deterministic for a given `seed`, and runs CPU-bound work inside `asyncio.to_thread`.

---

## 1. Folder layout

```
services/quant/
├── quant/
│   ├── main.py            # FastAPI app via create_service_app("quant")
│   ├── schemas.py         # request models (below)
│   ├── data.py            # get_prices(tickers, start, end, as_of) → DataFrame of adj close
│   ├── event_study.py
│   ├── correlations.py
│   ├── risk.py            # historical / parametric / Monte Carlo VaR + CVaR
│   ├── scenario.py        # factor shocks → P&L
│   ├── hedges.py          # beta & min-variance hedge sizing
│   ├── exposure.py        # sector weights, beta, weather/agri sensitivity, heatmap
│   └── evidence.py        # make_evidence(tool, value, source, as_of, confidence, ...)
├── tests/
│   ├── test_event_study.py  test_risk.py  test_hedges.py  test_scenario.py  test_exposure.py
│   └── synthetic.py       # synthetic price generators with known answers
├── requirements.txt       # fastapi uvicorn numpy pandas scipy yfinance httpx pydantic
└── README.md
data/lot_sizes.json          # {"^NSEI": 75, "^NSEBANK": 35, ...}  VERIFY on nseindia.com (they change)
data/sector_sensitivity.json # {"Energy":{"weather":0.6,"agri":0.1,"crude":0.8}, ...} judgment-based
```

---

## 2. Request models (`quant/schemas.py`)

Every request accepts optional `run_id`, `as_of: date | None` (time machine) and `seed: int = 42`.

```python
class EventStudyReq(BaseModel):
    ticker: str; event_date: date
    benchmark: str = "^NSEI"
    est_window: tuple[int, int] = (-120, -11)    # trading days relative to event
    event_window: tuple[int, int] = (-1, 5)
    as_of: date | None = None; run_id: str | None = None

class CorrReq(BaseModel):
    tickers: list[str]                   # e.g. ["RELIANCE.NS","CL=F","INR=X"]
    lags: list[int] = [0, 1, 2, 5]       # b lagged by k days vs a
    lookback_days: int = 250
    method: Literal["pearson", "spearman"] = "pearson"
    as_of: date | None = None; run_id: str | None = None

class RiskReq(BaseModel):
    portfolio: Portfolio
    method: Literal["historical", "parametric", "montecarlo"] = "montecarlo"
    horizon_days: int = 5
    confidence_level: float = 0.95
    n_paths: int = 10_000
    lookback_days: int = 500
    seed: int = 42
    as_of: date | None = None; run_id: str | None = None

class ScenarioReq(BaseModel):
    portfolio: Portfolio
    shocks: dict[str, float]     # factor → pct move, e.g. {"crude": 10, "monsoon_rain": -20, "usd_inr": 2, "nifty": -3}
    as_of: date | None = None; run_id: str | None = None

class HedgeReq(BaseModel):
    portfolio: Portfolio
    target: Literal["beta_neutral", "min_variance", "reduce_var_pct"] = "min_variance"
    reduce_var_pct: float = 30
    candidates: list[str] = ["^NSEI", "^NSEBANK"]   # hedge underlyings
    allow_options: bool = True
    horizon_days: int = 5
    as_of: date | None = None; run_id: str | None = None

class ExposureReq(BaseModel):
    portfolio: Portfolio
    benchmark: str = "^NSEI"
    as_of: date | None = None; run_id: str | None = None
```

---

## 3. Math (implement exactly; the tests check these)

**Returns.** Log returns `r_t = ln(P_t / P_{t-1})` for estimation and simple returns for P&L. Use adjusted close.

**Event study (market model).**
- Estimate `R_i,t = α + β·R_m,t + ε` on `est_window` with OLS.
- Abnormal return: `AR_t = R_i,t − (α̂ + β̂·R_m,t)` over `event_window`.
- `CAR = Σ AR_t`. `σ_AR` is the std of residuals in the estimation window. `t = CAR / (σ_AR·√L)`, where L is the event-window length. `p_value = 2·(1 − Φ_t(|t|, df=n_est−2))`.

**Lagged correlation.** `corr(r_a,t , r_b,t−k)` for each k in `lags`. Report n and flag `|corr| < 0.1` as weak.

**VaR / CVaR** (loss is positive, in INR, portfolio value `V = Σ qty·price`):
- *Historical.* Sum daily portfolio returns over the `horizon_days` window, using overlapping windows. `VaR = −quantile(R_h, 1−c)·V`, and `CVaR = −mean(R_h | R_h ≤ quantile)·V`.
- *Parametric.* `VaR = (−μ_h + z_c·σ_h)·V`, where `μ_h = μ·h` and `σ_h = σ·√h`, using portfolio μ and σ from the weights and covariance Σ.
- *Monte Carlo.* Draw `n_paths` multivariate normal daily returns `N(μ, Σ)` with a Cholesky factor of Σ (add 1e-10·I if Σ is not positive definite) over h days, compound them, and value the portfolio. `paths` returns 20 sample paths for charting, and `pnl_hist` returns 30 bins.

**Scenario (what-if).** Linear factor model: `ΔP_i/P_i = Σ_f β_i,f · shock_f`.
- Market-factor betas (`nifty`, `crude` = `CL=F`, `usd_inr` = `INR=X`, `us10y` = `^TNX`) are estimated by OLS of 250-day returns on the factor returns.
- Non-traded factors (`monsoon_rain`, `heatwave`, `agri_stress`) use `sector_sensitivity.json`: `β = sens × sign` (judgment). Mark these with `"method":"judgment"` in `by_ticker`.

**Beta hedge.** `β_p = Σ w_i β_i`. Hedge notional `= −β_p · V` in the index. Lots = `round(notional / (index_level · lot_size))`.

**Min-variance hedge ratio.** `h* = Cov(r_p, r_f) / Var(r_f)`. Notional = `h*·V`. Report `pre_var_inr` and `post_var_inr`, recomputing historical VaR of `r_p − h*·r_f`.

**Options alternative** (if `allow_options`). Protective put on the index, strike at 3–5% OTM, sized so that `delta·lots·lot_size·S ≈ β_p·V·0.5`. Price it with Black-Scholes using 20-day realized vol × 1.1 as a proxy for implied vol, risk-free rate 6.5%, expiry at the next monthly date. Label the result `est_cost_inr` as an **estimate**.

**Exposure.**
- `weight_i = qty·price / V`.
- `beta_i` vs the benchmark over 250 days.
- `weather_sens_i` and `agri_sens_i` come from `sector_sensitivity.json` for the holding's sector.
- `heatmap` is a matrix with rows = sectors, cols = `["weather","agri","crude","rates","usd_inr"]` and value = sector weight × sensitivity.

**Confidence rule** (written in code):
- `event_study`: 0.8 if `p<0.05`, otherwise 0.5.
- `risk`: 0.75 if `lookback ≥ 250` and `n_tickers_with_data == n_holdings`, otherwise 0.5.
- `scenario`: 0.6 for market factors, 0.4 if any judgment factor is used.
- `hedge`: 0.7.
- `exposure`: 0.8.
- `correlations`: 0.6.
- If any ticker's data is missing or comes from the cache, set `degraded=true` and multiply confidence by 0.5.

---

## 4. Build prompt (paste into a coding LLM)

```
You are building a FastAPI quant microservice "quant" for an Indian-market decision-support system.
It MUST use the shared package copilot_common (already installed): import Evidence, ToolResult,
Portfolio, Holding, HedgeProposal from copilot_common.models; create_service_app, mock_or, degraded
from copilot_common.service_base; cached from copilot_common.cache; EvidenceCounter from copilot_common.ids;
settings from copilot_common.settings.

Step 1. services/quant/requirements.txt: fastapi uvicorn numpy pandas scipy yfinance httpx pydantic.
Step 2. quant/schemas.py: request models EventStudyReq, CorrReq, RiskReq, ScenarioReq, HedgeReq,
        ExposureReq exactly as specified (pasted below).
Step 3. quant/data.py:
        async def get_prices(tickers: list[str], start: date, end: date, as_of: date|None) -> pd.DataFrame
        - First try POST {settings.INGEST_URL}/prices {"tickers":..., "start":..., "end":..., "as_of":...}
          (timeout 5s). Parse ToolResult; each evidence.value has rows.
        - On failure: yfinance.download(..., auto_adjust=True) via asyncio.to_thread, wrapped in cached("quant", key, fn).
        - Truncate to <= as_of. Return wide DataFrame (index=date, columns=tickers, values=adj close)
          plus a set of tickers that were degraded.
        def latest_prices(df) -> dict[str,float]
Step 4. Implement pure functions (no I/O) — these are what tests hit:
        event_study.run(prices: pd.Series, bench: pd.Series, event_date, est_window, event_window) -> dict
            returns {"ticker","event_date","window","alpha","beta","car","car_t","p_value","ar_series":[{"d":-1,"ar":...}]}
        correlations.run(returns: pd.DataFrame, lags, method) -> {"pairs":[{"a","b","lag","corr","n","weak"}]}
        risk.historical(returns, weights, V, h, c) / risk.parametric(...) / risk.montecarlo(..., n_paths, seed)
            each -> {"method","horizon_days","confidence_level","var_inr","cvar_inr","var_pct","paths","pnl_hist"}
        scenario.run(portfolio, prices, factor_returns, shocks, sensitivity) -> {"shocks","pnl_inr","pnl_pct","by_ticker":[...]}
        hedges.propose(portfolio, prices, candidates, target, lot_sizes, allow_options, h) ->
            {"proposals":[HedgeProposal dicts], "pre_var_inr", "post_var_inr", "portfolio_beta"}
        exposure.run(portfolio, prices, bench, sensitivity) -> {"by_sector","by_ticker","heatmap":{"rows","cols","values"}}
        Use the exact formulas in the spec's Math section. Monte Carlo: numpy default_rng(seed), Cholesky with
        jitter. All CPU work called via asyncio.to_thread from endpoints.
Step 5. quant/evidence.py: make_evidence(run_id, tool, value, source, as_of, confidence, degraded=False,
        degraded_reason=None, latency_ms=None) -> Evidence; summary is a code-written one-liner, e.g.
        f"5d 95% MC VaR ₹{var:,.0f} ({pct:.1%} of portfolio)".
Step 6. quant/main.py: app = create_service_app("quant"). Endpoints (all POST, all return ToolResult, all
        wrapped with mock_or("<endpoint>", real_fn)):
        /event_study (tool "event_study"), /correlations ("correlations"), /var_montecarlo ("risk"),
        /scenario ("scenario"), /hedge_proposals ("hedge"), /exposure ("exposure").
        Read X-Run-Id header for evidence ids. Never raise for data problems: return degraded evidence.
        422 only for invalid input.
Step 7. tests/synthetic.py: generators with KNOWN answers:
        - make_market(n=500, seed) -> bench returns N(0.0004, 0.01)
        - make_stock(bench, beta=1.3, alpha=0, noise=0.005) ; inject_event(stock, day, jump=0.05)
        Tests:
        - event study recovers beta within ±0.1 and CAR within ±0.01 of injected 5% jump, p<0.05.
        - parametric VaR of single asset N(0,0.01), h=1, c=0.95, V=1e6 ≈ 16,449 ±2%.
        - MC VaR within 5% of parametric for normal data; deterministic for same seed.
        - historical CVaR >= VaR.
        - beta hedge of portfolio = 1.0×index gives hedge_ratio ≈ 1 and post_var < 0.2×pre_var.
        - scenario: nifty -3% shock on beta-1.3 stock gives ≈ -3.9%.
        - exposure weights sum to 1.
        - /health returns ok; MOCK=1 /var_montecarlo returns fixture with degraded_reason "mock".
Step 8. README with run command: uvicorn quant.main:app --host 0.0.0.0 --port 8101
Output every file completely.
[PASTE §2 AND §3 OF 06_QUANT_ENGINE.md AND §5–7 OF 01_CONTRACTS.md]
```

---

## 5. Example inputs/outputs

### `POST /var_montecarlo`
```json
{"portfolio":{"portfolio_id":"demo","currency":"INR","cash":0,
  "holdings":[{"ticker":"RELIANCE.NS","qty":100,"sector":"Energy"},
              {"ticker":"ITC.NS","qty":400,"sector":"FMCG"},
              {"ticker":"HDFCBANK.NS","qty":150,"sector":"Banks"}]},
 "method":"montecarlo","horizon_days":5,"confidence_level":0.95,"n_paths":10000,"seed":42}
```
```json
{"evidence":[{"id":"ev_risk_007","run_id":"run_20261003141502_a91f","tool":"risk",
  "value":{"method":"montecarlo","horizon_days":5,"confidence_level":0.95,
           "var_inr":18420.0,"cvar_inr":23110.0,"var_pct":0.0331,
           "paths":[[1.0,0.996,1.004,0.991,0.987,0.992]],
           "pnl_hist":{"bin_edges":[-40000,-37000],"counts":[3]}},
  "summary":"5d 95% MC VaR ₹18,420 (3.3% of portfolio)",
  "source":"quant MC on yfinance adj close, 500d lookback","as_of":"2026-10-02T10:00:00Z",
  "timestamp":"2026-10-03T08:45:06Z","freshness_s":81906,"confidence":0.75,"degraded":false,
  "latency_ms":340,"model_version":"quant_v1"}],"warnings":[]}
```

### `POST /scenario` (what-if sliders)
```json
{"portfolio":{"holdings":[{"ticker":"RELIANCE.NS","qty":100,"sector":"Energy"},{"ticker":"ITC.NS","qty":400,"sector":"FMCG"}]},
 "shocks":{"crude":10,"monsoon_rain":-20}}
```
```json
{"evidence":[{"id":"ev_scenario_009","tool":"scenario",
  "value":{"shocks":{"crude":10,"monsoon_rain":-20},"pnl_inr":-6120.0,"pnl_pct":-0.0189,
   "by_ticker":[{"ticker":"RELIANCE.NS","pnl_inr":2210.0,"betas":{"crude":0.08},"method":"ols"},
                {"ticker":"ITC.NS","pnl_inr":-8330.0,"betas":{"monsoon_rain":0.06},"method":"judgment"}]},
  "summary":"Crude +10%, monsoon −20% → ₹-6,120 (−1.9%)","source":"quant factor model (OLS + judgment sensitivities)",
  "as_of":"2026-10-02T10:00:00Z","timestamp":"2026-10-03T08:45:07Z","confidence":0.4,"degraded":false}],
 "warnings":["monsoon_rain uses judgment sensitivity, not a measured beta"]}
```

### `POST /hedge_proposals`
```json
{"evidence":[{"id":"ev_hedge_010","tool":"hedge",
  "value":{"portfolio_beta":0.92,"pre_var_inr":18420.0,"post_var_inr":7900.0,
   "proposals":[
    {"hedge_id":"h1","instrument":"NIFTY OCT FUT short","underlying":"^NSEI","side":"sell","quantity":1,
     "unit":"lots","hedge_ratio":0.88,"est_cost_inr":null,"rationale":"min-variance h*=0.88 vs NIFTY over 250d",
     "sizing_method":"min_variance","evidence_ids":["ev_risk_007","ev_exposure_002"]},
    {"hedge_id":"h2","instrument":"NIFTY OCT 24500 PE","underlying":"^NSEI","side":"buy","quantity":1,
     "unit":"lots","hedge_ratio":0.45,"est_cost_inr":7350.0,"rationale":"4% OTM protective put, BS est with RV20×1.1",
     "sizing_method":"delta","evidence_ids":["ev_risk_007"]}]},
  "summary":"Min-variance NIFTY hedge cuts 5d VaR ₹18,420 → ₹7,900","source":"quant hedges_v1",
  "as_of":"2026-10-02T10:00:00Z","timestamp":"2026-10-03T08:45:08Z","confidence":0.7,"degraded":false}],
 "warnings":["Lot sizes from data/lot_sizes.json — verify on NSE before use. Paper trading only."]}
```

### `POST /exposure`
```json
{"evidence":[{"id":"ev_exposure_002","tool":"exposure",
  "value":{"by_sector":{"Energy":0.38,"FMCG":0.36,"Banks":0.26},
   "by_ticker":[{"ticker":"RELIANCE.NS","weight":0.38,"beta":1.05,"weather_sens":0.6,"agri_sens":0.1},
                {"ticker":"ITC.NS","weight":0.36,"beta":0.72,"weather_sens":0.4,"agri_sens":0.7},
                {"ticker":"HDFCBANK.NS","weight":0.26,"beta":0.98,"weather_sens":0.1,"agri_sens":0.3}],
   "heatmap":{"rows":["Energy","FMCG","Banks"],"cols":["weather","agri","crude","rates","usd_inr"],
              "values":[[0.23,0.04,0.30,0.08,0.11],[0.14,0.25,0.04,0.07,0.04],[0.03,0.08,0.03,0.18,0.05]]}},
  "summary":"Largest exposures: Energy 38%, FMCG 36% (agri-sensitive)","source":"quant exposure_v1 + sector_sensitivity.json (judgment)",
  "as_of":"2026-10-02T10:00:00Z","timestamp":"2026-10-03T08:45:04Z","confidence":0.8,"degraded":false}],"warnings":[]}
```

### `POST /event_study`
```json
{"evidence":[{"id":"ev_event_study_011","tool":"event_study",
  "value":{"ticker":"ONGC.NS","event_date":"2019-05-03","window":[-1,5],"alpha":0.0002,"beta":0.91,
           "car":-0.034,"car_t":-2.21,"p_value":0.029,"ar_series":[{"d":-1,"ar":-0.004},{"d":0,"ar":-0.012}]},
  "summary":"ONGC CAR(−1,+5) around Cyclone Fani = −3.4% (p=0.03)","source":"market model vs ^NSEI",
  "as_of":"2019-05-10T10:00:00Z","timestamp":"2026-10-03T08:45:09Z","confidence":0.8,"degraded":false}],"warnings":[]}
```

### `POST /correlations`
```json
{"evidence":[{"id":"ev_correlations_012","tool":"correlations",
  "value":{"pairs":[{"a":"RELIANCE.NS","b":"CL=F","lag":0,"corr":0.21,"n":249,"weak":false},
                    {"a":"RELIANCE.NS","b":"CL=F","lag":1,"corr":0.12,"n":248,"weak":false}]},
  "summary":"RELIANCE vs crude: corr 0.21 same-day, 0.12 at 1-day lag","source":"quant correlations_v1",
  "as_of":"2026-10-02T10:00:00Z","timestamp":"2026-10-03T08:45:09Z","confidence":0.6,"degraded":false}],"warnings":[]}
```

---

## 6. Mock mode

With `MOCK=1`, each endpoint returns `copilot_common/fixtures/quant/<endpoint>.json` (use the examples above) with `degraded=true` and `degraded_reason="mock"`. The fixture files are named `event_study.json`, `correlations.json`, `var_montecarlo.json`, `scenario.json`, `hedge_proposals.json` and `exposure.json`.

## 7. Acceptance checklist
- [ ] `pytest services/quant` passes, including all the synthetic known-answer tests
- [ ] `/var_montecarlo` with 3 holdings and 10k paths finishes in under 1.5 s on L2
- [ ] The same `seed` gives identical output, so the cache and backtest are reproducible
- [ ] Stopping ingestion still works: the service falls back to yfinance or the cache and sets `degraded=true`
- [ ] `as_of=2019-05-01` never uses prices after that date (test it)
- [ ] Every response validates as `ToolResult`

## 8. Integration hooks
- **Orchestrator (04).** `exposure_agent` calls `/exposure` during fan-out. `quant_agent` calls `/var_montecarlo` and then `/hedge_proposals` after join, passing `X-Run-Id`. It calls `/event_study` on the top analog dates returned by `find_analogs`.
- **Synthesizer (05 P8).** Hedges are passed through **as computed**, and the prompt forbids resizing them.
- **Validator (04).** The numbers in `summary` and `value` are what the Numbers Ledger matches against.
- **Terminal (13).** What-if sliders call `POST {ORCH_URL}/tools/scenario`, which the orchestrator proxies to here. Render `heatmap`, `paths` and `pnl_hist`.
- **Backtest (12).** Uses `event_study` and `scenario` with `as_of`.
- **Monitor (11).** Uses `/exposure` weights for impact scoring.

## 9. Hedge-review fixes (built)
A review of an alternative "risk vector + optimizer" hedge design found these problems: unitless risk scores multiplied into betas, noisy betas (RELIANCE crude β ≈ 0.08, not significant), no short positions, US futures that are not tradable in India, lot sizes ignored, thin validation, and LLM-written strength/confidence values leaking into the maths. This service already sized hedges as signed short NSE index futures and rounded to lots after sizing. The rest is fixed as follows.

| Phase | What | Where |
|---|---|---|
| — | **Beta shrinkage.** OLS beta shrunk toward the judgment prior with `w = t²/(t² + 2²)`. Each holding reports `beta_stats {beta_ols, t, n, weight_ols}`. A factor with `w < 0.5` counts as judgment (lower confidence, warning). On real data this moved RELIANCE crude from −0.043 (t −1.9) to +0.023, while the Nifty betas kept w ≥ 0.93 | `quant/scenario.py` |
| 1 | **Scenario from evidence.** `POST /scenario/from_evidence {portfolio, evidence[], horizon}` turns evidence *values* (never LLM text) into percent shocks: analogs raw-return p10/median/p90 for ^NSEI, CL=F/BZ=F and INR=X; macro "recent move persists" only where no analog covers the factor; agri stress = −yield q10/q50/q90; weather rain anomaly only for non-storm readings; all clipped. It returns the `/scenario` contract (median case) plus `cases`, `worst_case` and `shock_provenance`. Cases are comonotonic (stated). Degraded inputs make the result degraded | `quant/scenario_builder.py` |
| 2 | **Hedge back-check.** `POST /hedge_validation {portfolio, events[{event_id, event_date}]}`. For each past analog event, h* is fitted only on the 250 days *before* it, then hedged and unhedged max drawdown and return are compared over the next h days. Output: `n`, `n_improved`, median and range (p10–p90 if n ≥ 5, else min–max). Confidence is ≤ 0.5, or 0.3 if n < 5. Futures are modelled on spot (basis ignored, stated) | `quant/validation.py` |
| 3 | **Gated optimizer.** A ridge-regularised, signed, capped min-variance hedge over India-tradable instruments only (NIFTY/BANKNIFTY futures, NSE USDINR, MCX crude/gold with US prices as stated proxies). It is evaluated beside the baseline and reports `adopt: true` only if n ≥ 5 **and** it beats the baseline. `/hedge_proposals` keeps proposing the single-index baseline either way. On 6 real events it lost (median 2.3 pp vs 3.6 pp), so it is not adopted | `quant/validation.py` |

**Orchestrator wiring (04).**
- `quant_agent` calls `/scenario/from_evidence` whenever analogs, macro, agri or weather evidence exists. When a hedge is requested and the analogs carry `event_date`, it also calls `/hedge_validation`.
- The red team always adds a rule-based reason when n < 5 or the hedge improved half the events or fewer. That reason is never dropped by the LLM.
- The synthesizer receives pre-written, cited lines for the scenario range and the back-check.

**Real check (2026-10-03, RELIANCE/HDFCBANK/ITC, 6 Indian market events).** The baseline NIFTY hedge cut drawdown in 6/6 events, median +3.6 pp, p10–p90 +1.3 to +8.0 pp. This is a small sample and is reported as such.

## 10. Sources
- Event study methodology (MacKinlay 1997, "Event Studies in Economics and Finance", JEL).
- NSE F&O contract specifications and lot sizes: https://www.nseindia.com/products-services/equity-derivatives-contract-specifications. **Lot sizes are revised periodically, so verify them.**
- yfinance: https://github.com/ranaroussi/yfinance
