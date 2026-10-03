# Quant Engine (L2 :8101)

All numbers come from numpy/pandas/scipy; every endpoint returns `ToolResult` and is deterministic for a given `seed`.

## Run
```bash
pip install -e packages/copilot_common          # from repo root (the project's real package; quant/compat.py adapts)
pip install -r services/quant/requirements.txt
cd services/quant && uvicorn quant.main:app --host 0.0.0.0 --port 8101
pytest services/quant                           # from repo root
MOCK=1 uvicorn quant.main:app --port 8101       # fixtures, degraded_reason="mock"
```
Endpoints: `/event_study /correlations /var_montecarlo /scenario /scenario/from_evidence /hedge_proposals /hedge_validation /exposure /bs_price /health`.

## Hedge-review fixes (see docs/06 §9)
- **Beta shrinkage** (`scenario.py`): `w = t²/(t²+4)` toward the judgment prior; `beta_stats` per holding; w < 0.5 counts as judgment.
- **`/scenario/from_evidence`** (`scenario_builder.py`): evidence values → percent shocks (p10/median/p90), with provenance; no LLM numbers.
- **`/hedge_validation`** (`validation.py`): out-of-sample back-check on past analog events (n + range, low confidence when n < 5),
  plus a ridge optimizer over India-tradable instruments that is only `adopt: true` if n ≥ 5 and it beats the baseline.
- Integration with the project package: `quant/compat.py` adapts `mock_or` / `degraded`; `copilot_common.settings` exposes
  a live `settings` proxy and `get_data_dir()`. The data client uses the shared reachability circuit breaker.

## Decisions beyond the spec (please review)
- **Prices**: ingestion `/prices` first (5 s), else yfinance via `cached("quant", ...)`. Fallback or missing tickers => `degraded=true`, confidence x0.5. Uses `adj_close` if present else `close`.
- **V** = sum(qty x price); `cash` is excluded.
- **Scenario units**: `shocks` in percent points; `repo_bps` and `us10y` in basis points. `us10y` beta is OLS on yield *changes* (pp). Judgment beta = `sens x sign x scale[factor]`; `sign` and `scale` live under the reserved keys `_signs` / `_scale` in `sector_sensitivity.json` (all judgment, review them). A market factor with no data falls back to judgment and is flagged.
- **Hedge sizing**: min-variance uses overlapping `horizon_days` returns; beta uses 250d daily. If the hedge is under half a lot (or no lot size known) the proposal uses `unit="notional_inr"` instead of forcing 1 lot, and a warning says so. Otherwise lots are rounded (min 1) and `post_var_inr` uses the *effective* rounded ratio. Best candidate (lowest post-VaR) gets the single futures leg; `candidate_stats` lists all.
- **Options**: protective put 4% OTM, BS with 20d RV x 1.1, r = 6.5%, expiry = last Tuesday of the month (`QUANT_EXPIRY_WEEKDAY` to override; NSE moved from Thursday, so backtests before late 2025 get the wrong weekday).
- **Added optional fields**: `HedgeReq.evidence_ids` (cited in each proposal) and `/bs_price` (`BsPriceReq`). Propose both in 01 per its change process.
- `data/lot_sizes.json`: NIFTY 65, BANKNIFTY 30 (NSE revision, Jan-2026 series). Re-verify on nseindia.com.
- Not implemented: `data/ticker_aliases.json` (01 lists it under 06 but 06 does not describe it) and `X-Chaos` handling.
