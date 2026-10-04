# Delivery report: phases 0–6 (2026-10-04)

Everything below was run on one laptop: L1, RTX 4050 6 GB, with only `qwen3:4b-instruct` installed in Ollama. Nothing
was run across three laptops; see "Needs human input". Where something was not run, this report says so.

## What changed

| Phase | Commits | Summary |
|---|---|---|
| 0 Safety | 986f618, 9724d01 | git repo, `.gitignore` (secrets, runtime data, node/Next output), dead code moved to `_archive/`, SMTP secrets moved into the root `.env` |
| 1 Bugs | 8c2834f … 45dc7f7 | ingestion→sentiment 422; ticker validation; cold start (`keep_alive=-1`, warm-up); dead services now give "unavailable", never fixtures; DEMO storm gated behind `DEMO_MODE` and stamped SIMULATED; the type check fails the build; real per-node timings; weather region 422s |
| 2 Real data | 66c0419 … 23d3b83 | RBI repo history (BIS) and CPI (OECD/FRED) with provenance; measured crude sensitivities; agri deterioration metric and model card; MODIS refresh script |
| 3 Conversation | 849d78e | Greeting, help, thanks, out-of-scope, advice and unclear get fast replies with no LLM; follow-ups ("why?", "and for X?"); pet reactions |
| 4 Cluster | 88d0bee | `X-Cluster-Key` auth (HTTP + WebSocket); 2 retries on 502/503/504; GPU in `/health`; `/cluster/status`; the monitor pushes alerts to L1; Cluster page; `deploy/` (gen_cluster, make_bundles, verify_cluster) |
| 5 Usage | af1d780 | Run trace table plus `/llm/calls`; `scripts/verify_usage.py`; router keyword rules; Weaviate primary (auto-seed, parity test); real backtest; realised P&L and NSE holiday calendar. Bugs found and fixed: news filter, quant hedge crash, leaked answer JSON, agri on unmodelled regions |
| 6 Deliver | this commit | `README.md` (3-laptop setup, run order, troubleshooting), this report |

## Test counts

| Suite | Passed |
|---|---|
| backtest | 16 |
| deploy | 12 |
| evals (scorers) | 5 |
| infra | 4 |
| packages/copilot_common | 4 |
| packages/copilot_llm | 16 |
| services/agri | 43 |
| services/ingestion | 28 |
| services/monitor | 32 |
| services/orchestrator | 168 (includes the 67-test greeting/unclear/out-of-scope set) |
| services/quant | 46 |
| services/sentiment | 70 |
| services/vectordb | 142 (includes 17 live Weaviate parity tests) |
| **Total** | **586 passed, 0 failed** (baseline before phase 0: 434) |

The suites are run one at a time (one pytest process each), because they share module names. The TypeScript type
check (`tsc --noEmit`) passes.

## Trace coverage (`scripts/verify_usage.py`, final run, LLM cache off)

**Services:** all six were used.

| Service | Called by | Result |
|---|---|---|
| quant | exposure_agent, planner, quant_agent | used |
| sentiment | sentiment_agent | used (news now reaches it; see the fix below) |
| agri | agri_agent | used |
| vectordb | analog_agent (Weaviate) | used |
| ingestion | macro, sentiment, weather agents | used |
| monitor | conversation (active alerts) | used |

**Models:** one of four served its own role.

| Model | Role | Result |
|---|---|---|
| qwen3:4b-instruct | intent, synthesizer, explain, planner | used, 17 calls |
| gemma3:4b | narrators, sentiment second opinion | **not used**: not pulled. 13 calls fell back to qwen3:4b-instruct, marked `fallback` |
| phi4-mini | red team | **not used**: not pulled. 5 calls fell back, marked `fallback` |
| qwen3:1.7b | monitor alert text | **not used**: not pulled, and no alert fired during the run |

**Validator:** 7 of 8 questions passed. "Rank exposure" was flagged once, because the model wrote a malformed
citation `[ev_macro_00-01]`. The validator now ignores digits inside citations (test added). It was not re-run
against the live model after that fix.

`verify_usage` therefore still exits 1. That is correct: three models are missing. It will pass only once the models
are pulled on L2 and L3.

**Bugs found by the trace and fixed:**
- **News filter.** Ingestion kept an RSS headline only if it contained *every* word of the question, so live answers
  had 0 headlines and sentiment had nothing to score. It now matches any keyword or ticker.
- **Agri.** For Odisha it was routed but answered "no model". Monsoon and crop questions now use the 8 covered
  districts, labelled as such.
- **Leaked JSON.** qwen3 wrote the answer JSON in the middle of the markdown. Those blocks are now stripped. The
  synthesizer token budget went from 900 to 1400, because answers were being cut off.
- **Validator citations.** A figure printed in exactly one evidence summary is now auto-cited.

## Backtest: 8 held-out events

Held-out events are excluded from the analog index during the test, and the leakage guard is on. Horizon 5 days.
Results are in `data/backtest/scoreboard.json` and on the Backtest tab.

| Method | Hit rate | 95% CI | MAE | 80% band coverage | n |
|---|---|---|---|---|---|
| **Sigma** | **0.727** (8/11) | 0.45–0.91 | 0.033 | **0.27** | 11 of 16 points |
| Price trend only | 0.688 | 0.50–0.88 | 0.035 | 0.94 | 16 |
| Predict no change | – | – | 0.032 | 0.81 | 16 |
| News sentiment only | no data | – | – | – | 0 directional |

**Wins**
- Direction was right on 8 of 11 points, slightly better than the price trend. With 11 points, the confidence interval
  is too wide to call this better.
- These had the right direction: Hurricane Ida (NG=F, CL=F), Biparjoy (ADANIPORTS, Nifty), Michaung (Ashok Leyland,
  TVS), HUL in the 2015 monsoon deficit, and Brent after Abqaiq.

**Losses**
- **The bands are badly calibrated.** Only 27% of outcomes fell inside the 80% band. Several bands have zero width,
  because they were built from a single analog.
- M&M in the 2015 monsoon deficit: predicted +2.9%, actual −9.5%.
- Bank Nifty and HDFC Bank in the 2022 RBI hike: predicted +0.2%, actual −4.6%.
- 5 of 16 points had no prediction: NTPC and Coal India in the 2022 heatwave, BPCL after Abqaiq, KRBL and LT Foods
  after the rice export ban. Their analogs have no outcomes for those tickers.
- The sentiment baseline could not run. Historical GDELT news timed out or returned 429 for every case.
- 7 of 8 runs were degraded. In each, historical news was unavailable.

**Backtest bugs found and fixed**
- The backtest had been asking about the demo portfolio. 13 of 16 points were unscored; it now asks about the case's
  own assets.
- The quant engine crashed on a portfolio that holds the hedge index (`^NSEI`).

## Model cards

| Model | Card / evaluation | Headline |
|---|---|---|
| Crop stress `gbm_v2` | `services/agri/MODEL_CARD.md` | Accuracy 0.605 vs persistence 0.624, so it **does not beat persistence**. It catches deteriorations (recall 32.9%, precision 38.1%). It has low weight in answers and is never the lead signal |
| Sentiment `kdave/FineTuned_Finbert` | `services/sentiment/README.md`, `eval_report.json` | Accuracy 0.775, macro-F1 0.776 on 1,000 Indian financial headlines (majority baseline 0.338) |
| Embedder `BAAI/bge-small-en-v1.5` | `services/vectordb` | Weaviate and numpy return identical top-5 analogs in 16/16 parity cases |
| `qwen3:4b-instruct` prompts | `evals/README.md` | See the prompt evals below |

**Prompt evals (rerun 2026-10-04, local qwen3:4b-instruct).** The run was stopped by Claude Code after 9 of 10 cases
because the system was low on memory, so it wrote no result file.
- P1, P4, P5, P6, P7 and P9 passed on all 9 cases.
- **P8 (synthesizer) failed 3 of 9** (cases 2, 7, 9).
- gemma3, qwen3:1.7b and phi4-mini are not pulled, so the prompts on those models were run on qwen3.

## Cluster verification

- **On one laptop with a key** (`CLUSTER_KEY` set, all services restarted):
  - all 7 services answered 401 without the key and were accepted with it;
  - WebSockets were rejected without `?key=`;
  - service-to-service calls carried the key;
  - a full query ran end to end;
  - the CORS preflight for `X-Cluster-Key` passed.
- **`verify_cluster` (loopback):** 8 of 10 checks passed. The two failures were L2 and L3 Ollama (models missing).
- **Not run:** a real 3-laptop network, the L2/L3 bundles on other machines, and the Cluster page in a browser (the
  Chrome extension was not connected). The key was confirmed in the compiled app bundle, and the endpoint works.

## NEEDS HUMAN INPUT

1. **Pull the models** on their laptops:
   - L2: `ollama pull gemma3:4b`
   - L3: `ollama pull qwen3:1.7b` and `ollama pull phi4-mini`

   Then re-run `scripts/verify_usage.py` and `evals/run_evals.py`. Until then those roles run on L1's model, marked
   `fallback`.
2. **Run the 3-laptop setup for real:** `deploy/gen_cluster.ps1` with the real IPs, then the bundles, then
   `deploy/verify_cluster.ps1`. It is untested across machines.
3. **Re-run the evals** when memory allows: `.venv\Scripts\python evals\run_evals.py`. The last run was stopped at 9/10.
4. **NASA Earthdata credentials** (`EARTHDATA_USERNAME` / `EARTHDATA_PASSWORD` in `.env`) for
   `services/agri/scripts/refresh_modis.py`. The imagery is 274 days old (latest composite 2026-01-03). The AppEEARS
   path is untested.
5. **CPI:** the file uses the OECD/FRED series, which ends in March 2025 and differs from MoSPI's index. Newer MoSPI
   CPI needs a manual download, because the MoSPI API parameters could not be discovered.
6. **Historical news for the backtest:** GDELT timed out or returned 429, so the sentiment baseline and 7/8 runs lack
   news. A news archive or GDELT at a slower rate is needed.
7. **Real tier-3 alert emails are enabled.** The SMTP credentials are in the root `.env`. Confirm the recipient
   (`ALERT_EMAIL_TO`) before the demo.
8. **The cluster key is in the desktop app bundle** (`NEXT_PUBLIC_CLUSTER_KEY`). It keeps other devices on the
   network out; it is not a user password. Do not share the L3 bundle.
9. **Backtest misses** (`misses` in the scoreboard) are marked "PENDING HUMAN REVIEW": M&M 2015 and the banks in the
   RBI hike. Read their traces (`GET /runs/{id}/trace`) and write the reason.
10. **NSE holidays after 2026-12-31** are not in `exchange_calendars` 4.13. Past that date, every weekday counts as a
    trading day and is flagged as such. Update the library in 2027.
