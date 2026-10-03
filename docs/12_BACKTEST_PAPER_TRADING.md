# 12 — Backtest Scoreboard, Calibration & Paper Trading

| | |
|---|---|
| **Owner / Laptop** | Quant/ML person · backtest runs on **L2** (it calls the L1 orchestrator), paper trading is hosted **inside the L1 orchestrator** · folders `backtest/` and `services/orchestrator/paper/` |
| **Depends on** | [01_CONTRACTS.md](01_CONTRACTS.md), [04 Orchestrator](04_ORCHESTRATOR_LANGGRAPH.md) (`POST /query` with `as_of`), [08 Vector DB](08_VECTOR_DB.md) (holdout split and leakage filter), [10 Ingestion](10_DATA_INGESTION.md) (`as_of` time machine, `/prices`), [06 Quant](06_QUANT_ENGINE.md) (hedges, pricing helpers) |
| **Provides** | `data/backtest/scoreboard.json` (served at `GET {ORCH_URL}/backtest/scoreboard`), reliability-plot data, honest-misses table, and paper-trading endpoints `/paper/propose`, `/paper/approve`, `/paper/positions`, `/paper/mark`, `/paper/history` |
| **Used by** | [13 Terminal](13_FRONTEND_TERMINAL.md) (Backtest page, Paper-trades page, hedge "Approve" button) |

**Why this module matters to judges:** it shows the system is measured, not just plausible. **Report misses honestly.** A scoreboard that admits 2 of 7 misses is more credible than a perfect one.

---

## Part A — Backtest

### A1. Held-out events

**Source of truth:** `data/events.json` (owned by 08). Each held-out record has `"split": "holdout"` (the others have `"split": "train"`), and the vector DB must **never** return holdout events during a backtest (see A3). This set is the same 8 events listed in 08 §4.2:

| event_id | Event | Event date | `as_of` (no data after this) | Target assets (scored) | Query sent to the system |
|---|---|---|---|---|---|
| `hurricane_ida_2021` | Hurricane Ida, Cat 4, Louisiana | 2021-08-29 (Sun) | 2021-08-29 | `NG=F`, `CL=F` | "Category 4 hurricane heading to Louisiana refineries. Impact on energy holdings over 5 days?" |
| `cyclone_biparjoy_2023` | Cyclone Biparjoy, Kutch landfall | 2023-06-15 | 2023-06-14 | `ADANIPORTS.NS`, `^NSEI` | "Severe cyclone approaching Kutch, Gujarat. Impact on my portfolio over 5 days?" |
| `cyclone_michaung_2023` | Cyclone Michaung, Chennai | 2023-12-05 | 2023-12-04 | `ASHOKLEY.NS`, `TVSMOTOR.NS` | "Severe cyclone hitting Chennai, an auto manufacturing hub. Impact on auto holdings over 5 days?" |
| `monsoon_deficit_2015` | 2015 monsoon deficit (−14%) | 2015-08-01* | 2015-07-31 | `^CNXFMCG`, `M&M.NS` | "Monsoon running well below normal. Impact on FMCG and rural names over 20 days?" |
| `heatwave_2022` | North/central India heatwave | 2022-04-28* | 2022-04-27 | `NTPC.NS`, `COALINDIA.NS` | "Severe heatwave and record power demand across north India. Impact on power holdings over 5 days?" |
| `rbi_offcycle_hike_2022` | RBI off-cycle +40 bp hike | 2022-05-04 | 2022-05-03 | `^NSEBANK`, `HDFCBANK.NS` | "Inflation is running hot, with a risk of a surprise RBI hike. Impact on bank holdings over 5 days?" |
| `abqaiq_attack_2019` | Abqaiq attack, Brent spike | 2019-09-16 (first trading day) | 2019-09-15 | `BZ=F`, `BPCL.NS` | "Drone attack knocks out Saudi oil output. Impact on refiners and my portfolio over 5 days?" |
| `rice_export_ban_2023` | India bans non-basmati rice exports | 2023-07-20 (evening) | 2023-07-20 | `KRBL.NS`, `LTFOODS.NS` | "India bans rice exports. Impact on rice exporters over 5 days?" |

Here t0 is the first trading day at or after the event date, and `realized` is measured from the close on the last trading day ≤ `as_of`. Dates marked `*` are approximate. Check every date against IMD, NOAA or RBI before you run. Score horizon: `horizon_days` from the query (default 5 trading days).

### A2. What gets predicted (structured, not free text)

The backtest **never parses LLM prose**. It reads structured fields from the `FinalAnswer` and `Evidence`:

| Prediction field | Taken from |
|---|---|
| `pred_median` | `analogs` Evidence → `value.distribution` is a **list** with one entry per asset (see 08). Pick the entry where `asset == target_asset` and `horizon == "5d"`, then read `.median`. If the asset is missing, fall back to `holdings_impact[].range` midpoint |
| `pred_p10`, `pred_p90` | Same entry's `.p10/.p90` (and `.conformal_lo/.conformal_hi` if present, scored separately) |
| `pred_direction` | `sign(pred_median)`, or `0` if `|pred_median| < 0.002` |
| `pred_confidence` | `FinalAnswer.confidence` mapped as low 0.55, medium 0.65, high 0.8 (the stated probability that the direction is right) |
| `realized` | `close[t0 + h] / close[t0 − 1] − 1` from `/prices` **without** `as_of` (the only place future data is used) |

### A3. Leakage rules (enforced in code and tested)
1. Every call to the orchestrator passes `as_of`. Ingestion (10) filters all data to dates ≤ `as_of`.
2. The vector DB `find_analogs` request carries `as_of` and `exclude_holdout: true` (08 §5). Analogs whose `start_date > as_of` are also excluded.
3. News is taken only from GDELT with `enddatetime = as_of` (live RSS has no history).
4. Run with `CACHE_MODE=record` once, then `replay`, so reruns are identical and free.
5. Run with `LLM_MODE=local` to save cloud quota (optionally one `boost` run for comparison).
6. A test asserts that no Evidence in any backtest run has `as_of > run.as_of`.

### A4. Baselines (same events, same metric code)
| Baseline | Prediction |
|---|---|
| `price_only` (momentum) | `direction = sign(20-day return before as_of)`; `median = direction × mean(|5d return|, past 250 days)`; interval = ±1.28 × the std of 5d returns (an 80% normal interval) |
| `sentiment_only` | `direction = sign(portfolio_sentiment or by_ticker score from the 07 sentiment tool, using GDELT headlines ≤ as_of)`; same magnitude and interval as price_only |
| `zero` (no-change) | `median = 0`, interval as above. This is the honest "do nothing" reference for MAE |

### A5. Metrics (`backtest/metrics.py`)
```python
hit_rate     = mean(pred_direction == sign(realized))         # excluding pred_direction == 0, report n
mae          = mean(|pred_median - realized|)
coverage_80  = mean(pred_p10 <= realized <= pred_p90)          # target ≈ 0.80
brier        = mean((pred_confidence - 1[direction correct])**2)
skill_vs_zero = 1 - mae / mae_zero                               # > 0 means better than no-change
```
With only about 14 (event × asset) points, **report n and a bootstrap 90% CI** (`numpy`, 2,000 resamples) for hit rate and MAE. Don't claim significance.

### A6. Calibration / reliability plot
Bin `pred_confidence` into {0.55, 0.65, 0.8} and plot the observed hit rate per bin with n labels. Also plot interval coverage at 80%. Store this data in the scoreboard (`calibration` block). The frontend draws it, with a diagonal "perfectly calibrated" line.

### A7. Runner (`backtest/run_backtest.py`)
```
python -m backtest.run_backtest --events data/events.json --split holdout --mode local --cache replay
  for each event:
    1. POST {ORCH_URL}/query {"query":..., "as_of":..., "llm_mode":"local", "portfolio": demo}
    2. wait on WS until "final" (timeout 120 s) or GET /runs/{run_id}
    3. extract the prediction (A2); compute baselines (A4); fetch realized (A2)
  compute metrics per method, calibration, misses → data/backtest/scoreboard.json
  print a rich table
```
Runs sequentially (the GPUs are shared) and logs each run_id so the UI can open the full trace of any backtest row.

### A8. Scoreboard JSON (contract for the 13 Backtest page)
```json
{
  "generated_at": "2026-10-03T06:00:00Z",
  "config": {"llm_mode": "local", "horizon_days": 5, "split": "holdout", "n_events": 8, "n_points": 16},
  "methods": {
    "copilot":        {"hit_rate": 0.67, "hit_rate_ci": [0.42, 0.85], "mae": 0.031, "coverage_80": 0.71, "brier": 0.21, "skill_vs_zero": 0.18, "n": 12},
    "price_only":     {"hit_rate": 0.50, "hit_rate_ci": [0.25, 0.75], "mae": 0.039, "coverage_80": 0.79, "brier": 0.25, "skill_vs_zero": -0.03, "n": 14},
    "sentiment_only": {"hit_rate": 0.57, "hit_rate_ci": [0.33, 0.79], "mae": 0.037, "coverage_80": 0.79, "brier": 0.24, "skill_vs_zero": 0.02, "n": 14},
    "zero":           {"hit_rate": null, "mae": 0.038, "coverage_80": 0.79, "n": 14}
  },
  "rows": [
    {"event_id": "hurricane_ida_2021", "asset": "NG=F", "as_of": "2021-08-27", "run_id": "run_20261003055901_77aa",
     "copilot": {"median": 0.048, "p10": -0.012, "p90": 0.10, "direction": 1, "confidence": 0.65},
     "price_only": {"median": 0.031, "direction": 1}, "sentiment_only": {"median": -0.031, "direction": -1},
     "realized": 0.071, "hit": true, "in_interval": true}
  ],
  "calibration": [
    {"stated": 0.55, "observed": 0.50, "n": 4},
    {"stated": 0.65, "observed": 0.71, "n": 7},
    {"stated": 0.80, "observed": 1.00, "n": 1}
  ],
  "misses": [
    {"event_id": "rbi_offcycle_hike_2022", "asset": "^NSEBANK", "pred_median": 0.012, "realized": -0.018,
     "why_missed": "Off-cycle hike was a true surprise; analogs were mostly scheduled policy moves. Analog match similarity 0.61 (low).",
     "run_id": "run_20261003060412_1c0e"}
  ],
  "disclaimer": "8 events, 16 points. Small sample; decision support, not trading signals."
}
```
(These numbers are illustrative placeholders. Publish the real output only.)

`why_missed` is written **by a human** after looking at the trace, or drafted by `qwen3:4b-instruct` from the stored run and then edited. Keep it factual.

### A9. Backtest build prompt
```
Build a Python backtest harness (package backtest/) for a market decision-support system.
It calls an HTTP API: POST {ORCH_URL}/query (QueryRequest with as_of, llm_mode) → {run_id, ws_url};
GET {ORCH_URL}/runs/{run_id} → {final: FinalAnswer, events: [...]}. Models are in copilot_common (pasted below).

Step 1. backtest/events.py: load data/events.json, filter split=="holdout", expose BacktestCase(event_id, t0, as_of,
        assets, query, horizon_days).
Step 2. backtest/client.py: async run_case(case) → FinalAnswer (poll GET /runs/{run_id} every 1 s, 120 s timeout).
Step 3. backtest/extract.py: extract_prediction(final, asset, horizon) per spec A2 (read analogs Evidence
        value.distribution; fallback holdings_impact). Never parse prose.
Step 4. backtest/realized.py: realized_return(asset, t0, h) using {INGEST_URL}/prices WITHOUT as_of
        (close[t0+h]/close[t0-1]-1, trading days).
Step 5. backtest/baselines.py: price_only, sentiment_only, zero exactly as spec A4 (data strictly <= as_of).
Step 6. backtest/metrics.py: hit_rate, mae, coverage_80, brier, skill_vs_zero, bootstrap CI (2000 resamples, seed 42).
Step 7. backtest/calibration.py: bins + observed rates.
Step 8. backtest/run_backtest.py: CLI (argparse: --events --split --mode --cache --horizon), sequential runs,
        writes data/backtest/scoreboard.json in EXACTLY the schema of spec A8, prints a rich table.
Step 9. tests: leakage test (assert every Evidence.as_of <= case.as_of in each stored run), metrics on hand-made arrays,
        extraction on a fixture FinalAnswer, baseline uses no data after as_of.
Complete files, no TODOs.
[PASTE 01_CONTRACTS.md §5 + this file Part A]
```

---

## Part B — Paper trading with human approval (inside the orchestrator, L1)

### B1. Rules
- **No real orders, ever.** No broker API in this flow (the Angel One import in 13 is read-only).
- A hedge from `FinalAnswer.hedges` can only become a position after an explicit **human approval click** in the terminal (`approved_by` is required). There is no auto-approve.
- Prices: futures and stocks are marked at the yfinance close of the underlying (an index future is proxied by `^NSEI` spot, labelled **"spot proxy"**). Options are marked with **Black–Scholes** (`06` exposes `bs_price(S, K, T, r, sigma, kind)`), with `sigma = 1.1 × 20-day realised vol`, labelled **"model price"**. Never call it a market price.
- Lot sizes live in `data/lot_sizes.json` (NIFTY 65 from the January 2026 series). **Verify the current NSE lot sizes before the demo**, because NSE revises them.

### B2. SQLite tables (`data/ledger.db`, same DB as the run ledger)
```sql
CREATE TABLE paper_proposals (
  proposal_id TEXT PRIMARY KEY, run_id TEXT, hedge_json TEXT, status TEXT CHECK(status IN ('pending','approved','rejected','expired')),
  created_at TEXT, decided_at TEXT, approved_by TEXT, note TEXT);
CREATE TABLE paper_positions (
  position_id TEXT PRIMARY KEY, proposal_id TEXT, instrument TEXT, underlying TEXT, side TEXT,
  quantity REAL, unit TEXT, entry_price REAL, entry_ts TEXT, price_kind TEXT,  -- 'close' | 'spot_proxy' | 'model_price'
  status TEXT CHECK(status IN ('open','closed')), exit_price REAL, exit_ts TEXT);
CREATE TABLE paper_marks (
  position_id TEXT, ts TEXT, mark_price REAL, pnl_inr REAL, portfolio_pnl_unhedged_inr REAL, portfolio_pnl_hedged_inr REAL,
  PRIMARY KEY (position_id, ts));
```

### B3. Endpoints (FastAPI router `services/orchestrator/paper/router.py`, mounted at `/paper`)
| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/paper/propose` | `{"run_id": "...", "hedge_id": "h1"}` | proposal (`status: pending`). The hedge is copied **exactly** from the stored FinalAnswer, never resized |
| POST | `/paper/approve` | `{"proposal_id": "...", "decision": "approve"\|"reject", "approved_by": "Nitish", "note": "..."}` | proposal, plus a new position if approved (entry price = latest mark) |
| GET | `/paper/positions` | `?status=open` | positions with latest mark and P&L |
| POST | `/paper/mark` | `{}` (also runs automatically every 15 min during market hours and at 15:35 IST) | marks written |
| POST | `/paper/close` | `{"position_id": "..."}` | closed position |
| GET | `/paper/history` | `?run_id=` | proposals, positions and marks for audit |

Outcome tracking: each mark stores the **hedged vs unhedged portfolio P&L** since entry, so the UI can show "the hedge reduced drawdown by ₹X" or "the hedge cost ₹Y". Both outcomes get shown.

### B4. Example
`POST /paper/approve` response:
```json
{
  "proposal": {"proposal_id": "pp_20261003_b81e", "run_id": "run_20261003141502_a91f", "status": "approved",
               "approved_by": "Nitish", "decided_at": "2026-10-03T09:10:44Z", "note": "demo"},
  "position": {"position_id": "pos_20261003_0c3a", "instrument": "NIFTY OCT FUT short", "underlying": "^NSEI",
               "side": "sell", "quantity": 1, "unit": "lots", "entry_price": 25210.5, "price_kind": "spot_proxy",
               "entry_ts": "2026-10-03T09:10:44Z", "status": "open"}
}
```
`GET /paper/positions`:
```json
[{"position_id": "pos_20261003_0c3a", "instrument": "NIFTY OCT FUT short", "quantity": 1, "unit": "lots",
  "entry_price": 25210.5, "last_mark": 25020.0, "price_kind": "spot_proxy", "pnl_inr": 14287.5,
  "portfolio_pnl_unhedged_inr": -21400.0, "portfolio_pnl_hedged_inr": -7112.5, "marked_at": "2026-10-04T10:00:00Z"}]
```
(Illustrative numbers. P&L = (entry − mark) × lot size × lots for a short.)

### B5. Paper-trading build prompt
```
Add a paper-trading module to an existing FastAPI orchestrator (services/orchestrator). Python 3.11, aiosqlite.
Shared models: HedgeProposal, FinalAnswer from copilot_common (pasted). Runs are stored in data/ledger.db
(table runs(run_id, final_json, ...)) by the orchestrator.

Step 1. paper/db.py: create the three tables in spec B2 if missing.
Step 2. paper/pricing.py: mark_price(instrument, underlying, kind) → (price, price_kind) using {INGEST_URL}/prices
        (latest close; ^NSEI for index futures = spot_proxy) and {QUANT_URL} bs_price for options (model_price,
        sigma = 1.1 * 20d realised vol). lot_size(symbol) from config/lot_sizes.json.
Step 3. paper/router.py: endpoints exactly as spec B3. propose copies the hedge from the stored FinalAnswer by hedge_id
        (404 if absent; never modify quantity). approve requires approved_by (422 otherwise). P&L sign conventions
        for buy/sell, lots × lot_size.
Step 4. paper/scheduler.py: mark job every 15 min 09:15–15:30 IST and at 15:35 IST; also computes hedged vs unhedged
        portfolio P&L since entry (portfolio from the run's FinalAnswer).
Step 5. Mount router at /paper. Add GET /backtest/scoreboard serving data/backtest/scoreboard.json (404 → {"status":"not_run"}).
Step 6. tests: propose→approve→position created; reject → no position; propose with unknown hedge_id → 404;
        approve without approved_by → 422; P&L sign for short futures; option uses model_price label.
Complete files, no TODOs.
```

---

## Mock mode
- `MOCK=1`: `GET /backtest/scoreboard` returns `fixtures/orchestrator/scoreboard.json` (the A8 example). `/paper/*` works normally against a temporary SQLite with mock prices (`pricing.py` returns fixture closes).
- The frontend (13) can build the Backtest and Paper pages using only these fixtures.

## Acceptance checklist
Status 2026-10-03 (details in `backtest/README.md`): replay twice identical ✓ · leakage test ✓ (as_of + holdout analogs) ·
scoreboard shape ✓ · paper propose → approve → mark → positions ✓, reject creates no position ✓, quantity always equals the hedge ✓ ·
honest-miss `why_missed` ✗ pending (needs real predictions from 06/08) · real scoreboard ✗ pending 06/08/10.
Lot sizes from the Jan 2026 series: NIFTY 65, BANKNIFTY 30 (not 75/35). `^CNXFMCG` has no Yahoo history → HINDUNILVR.NS proxy.

- [ ] `run_backtest --cache replay` on a warmed cache completes all 8 events with zero network calls and identical output twice
- [ ] The leakage test passes (no Evidence `as_of` after the case `as_of`; no holdout event among the analogs)
- [ ] `scoreboard.json` validates against the A8 shape and includes baselines, calibration and misses
- [ ] At least one honest miss is documented with a `why_missed`
- [ ] The paper flow propose → approve → mark → positions works from the terminal; reject creates no position
- [ ] Hedge quantity in a position always equals the FinalAnswer hedge quantity

## Integration hooks
- **04:** must accept `as_of` and `llm_mode` in `QueryRequest`, and must store the full FinalAnswer per run (`GET /runs/{id}`). Mounts `/paper` and `/backtest/scoreboard`.
- **06:** expose `bs_price` (endpoint `POST /bs_price` or importable function) and keep `HedgeProposal.hedge_id` stable within a run.
- **08:** `find_analogs` honours `as_of` and `exclude_holdout`, and `events.json` marks `split`.
- **10:** honours `as_of` on every endpoint, and GDELT supports `enddatetime`.
- **13:** Backtest page (method comparison table, rows linking to `/run/{run_id}`, reliability plot, misses), Paper page (positions table, P&L chart), and an "Approve hedge" button on the answer view calling `/paper/propose` then `/paper/approve`.

## Sources
- [Open-Meteo Historical Forecast and Historical Weather APIs](https://open-meteo.com/en/docs/historical-weather-api) (no look-ahead weather for `as_of`)
- [GDELT DOC 2.0 API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/) (date-bounded news)
- Brier score and reliability diagrams (standard forecast verification), NSE lot sizes (https://www.nseindia.com/), RBI press releases (https://www.rbi.org.in/). Verify the event dates.
