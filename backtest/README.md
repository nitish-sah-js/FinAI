# Backtest + paper trading (docs/12)

Integrated from `backtester_nitr/` (2026-10-03). Code lives in two places, as doc 12 specifies:
- **`backtest/`** (repo root): Part A, the scoreboard. Runs on any laptop and calls the orchestrator over HTTP.
- **`services/orchestrator/paper/`**: Part B, paper trading. Mounted at `/paper` inside the orchestrator, with the mark scheduler started with the app.

```powershell
# 1. orchestrator (same CACHE_MODE / LLM_MODE as the run)
cd services; ..\.venv\Scripts\python -m uvicorn orchestrator.app:app --host 0.0.0.0 --port 8000
# 2. backtest: record once, then replay (identical and free)
.venv\Scripts\python -m backtest.run_backtest --mode local --cache record
.venv\Scripts\python -m backtest.run_backtest --mode local --cache replay       # → data/backtest/scoreboard.json
.venv\Scripts\python -m pytest backtest/tests -q                               # 16 tests
```
`--limit N` runs only the first N events. `--out PATH` writes the scoreboard somewhere else.

## Verified 2026-10-03 (L1, qwen3:4b-instruct)
- All 8 held-out events ran end to end (about 30 s each with the local LLM, about 6 s each from the cache). The leakage guards passed: no Evidence `as_of` after the case `as_of`, and no held-out event among the analogs.
- 16 realized returns came from real market data (yfinance; ingestion is not built yet).
- `--cache replay` twice gave identical output, also identical to the record run (doc 12 acceptance ✓).
- Paper flow through the real orchestrator works: run → hedge → propose → approve → mark → positions → history. The quantity always equals the FinalAnswer hedge.

**The copilot row is not a result yet.** The analog search (08) and quant engines (06) are not built, so predictions come from fixtures, and only 2 of 16 points had one. The pipeline-check scoreboard was deliberately **not** written to `data/backtest/scoreboard.json`. Run the real backtest once 06, 08 and 10 exist. The baselines are already real: `price_only` hit 0.69 over 16 points, but MAE 0.0347 against 0.0315 for "no change".

## What was missing or wrong in `backtester_nitr/`
1. **6 modules were missing** (imports failed): `events`, `baselines`, `metrics`, `calibration` (backtest) and `db`, `scheduler` (paper). They were written to match the behaviour the original tests pin; all 17 original tests pass unchanged.
2. **The sentiment baseline could never work.** It posted `{"tickers": ...}`, but `/sentiment/score` needs the headlines. It now fetches news ≤ as_of from ingestion first, and reports `sentiment_unavailable` honestly.
3. **`exclude_holdout` was not in the contract.** The backtest could have "found" the event it was scoring. It was added to `QueryRequest` (01) and is sent by the client; the runner also rejects any held-out analog.
4. **holdings_impact fallback parsing.** The orchestrator writes ranges as strings ("-4.1% to +1.2% (5d)"); the fallback only parsed lists.
5. **Lot sizes.** The real `data/lot_sizes.json` now holds the 2026 NSE values (NIFTY 65, BANKNIFTY 30, FINNIFTY 60, MIDCPNIFTY 120). An unknown symbol raises instead of silently using 1 (which made stock-option P&L ~500× too small).
6. **Approval with no price** used to half-create a position. Now it returns 503 or 422 and the proposal stays pending.
7. **`n` misreported.** zero and sentiment_only showed n=0. `n` now counts scored points, and `n_directional` counts non-zero calls.
8. **`^CNXFMCG` has no Yahoo history.** The monsoon case uses HINDUNILVR.NS as a proxy (noted in `data/events.json`).
9. Prices go through the shared `copilot_common.prices`: ingestion `/prices` first, yfinance as the fallback, cached for replay. Black–Scholes uses quant `/bs_price` when it is up, else the same formula locally (still labelled "model price").

`data/events.json` holds only the 8 held-out events (from doc 12 §A1). Doc 08 adds the training events.
