# 16 — Improvements and Stretch Backlog

These are the upgrades over the original plan. **Most of them are already written into the module specs** as numbered build steps with acceptance tests. This file is the index, plus a backlog of extras if you finish early.

## A. Built into the module specs

| # | Improvement | Why it matters to judges | Where it's built | Effort |
|---|---|---|---|---|
| 1 | **Groq replaces Kimi as the cloud boost model.** Fallback chain: Groq `gpt-oss-120b` → Groq `qwen3-32b` → Cerebras → OpenRouter Kimi → local `qwen3:4b-instruct` | About 20× more free requests than Kimi's OpenRouter tier and much faster answers. The app never stops working | 03 | S |
| 2 | **Quota meter and "LLM calls saved" counter** | Shows cost-aware engineering | 03 (logic), 13 (UI) | S |
| 3 | **Deterministic router with `Send` fan-out**, blocking work moved into `asyncio.to_thread` | Real parallelism, so total latency is the slowest agent rather than the sum | 04 | M |
| 4 | **LangGraph SQLite checkpointer** (resume a run if a laptop drops) | Robustness on stage | 04 | S |
| 5 | **Per-agent token and latency budget** with timeouts, after which the agent is marked degraded | Predictable demo timing | 04, 13 | S |
| 6 | **Freshness badges and a staleness penalty** (`Evidence.as_of`, `freshness_s`, `staleness_factor`) | Honest confidence, since old data counts less | 01, 04, 13 | S |
| 7 | **"Time machine" mode** (`as_of` on every query) | The same code path powers the backtest, so it is provably free of look-ahead | 04, 10, 12 | M |
| 8 | **Conformal intervals on analog outcomes** | Ranges with a coverage guarantee, not just p10/p90 | 08 | M |
| 9 | **Calibration reliability plot** from the backtest | Shows that "70% confidence" really means 70% | 12, 13 | S |
| 10 | **What-if sliders** (crude ±%, monsoon deficit %, INR ±%) calling `/scenario` live | Interactive and visual. Judges get to play with it | 06, 13 | M |
| 11 | **Sector heatmap** of weather and agri sensitivity across the portfolio | Instant visual link from the ISRO signal to holdings | 06, 13 | S |
| 12 | **Hindi and Hinglish answer toggle** | Fits the Indian-market theme and serves retail users | 05 (P8 variant), 13 | S |
| 13 | **NSE/BSE corporate announcements and IMD cyclone bulletins** as event sources | India-specific data instead of only US data | 10 | M |
| 14 | **Prompt-eval harness** (10 golden queries, schema validity, cited-number rate) | Catches prompt regressions in seconds | 05 | S |
| 15 | **Record and replay for every external call** (`CACHE_MODE`) | Unlimited free reruns and an offline demo | 01, 03, 10 | S |
| 16 | **Chaos toggles** (weather down, forced 429, missing raster, vector DB down, slow network) | Live proof of graceful degradation | 01, 04, 13, 15 | S |

S means less than 2 hours, M means 2–5 hours.

## B. Stretch backlog (only after the freeze-critical items work)

| Idea | Notes |
|---|---|
| **Agent-to-agent (A2A) cards** for each L2/L3 service | Expose `/.well-known/agent.json` describing each tool. Good "multi-agent" talking point, and the existing HTTP stays. |
| **Monsoon progress tracker panel** | Daily IMD cumulative rainfall versus the long-period average, per subdivision, with linked holdings. |
| **News de-duplication with embeddings** | Cluster near-duplicate headlines (cosine > 0.9) before sentiment, so a burst isn't counted five times. |
| **Explain-yourself diff** | Run the same query with `as_of = yesterday` and today, then show which evidence changed and why the answer moved. |
| **Portfolio import from broker CSV** (Zerodha Console, Groww export) | Safer than API credentials. Parse their holdings CSV format. |
| **Voice query** | Browser Web Speech API sends text to `/query`, with Hindi supported. |
| **Speculative local draft** | Show the local `qwen3:4b-instruct` draft immediately, then replace it with the Groq answer when it arrives. |
| **Mini fine-tune of the narrator** | LoRA on about 200 (evidence → narration) pairs generated during development. Only do this if there's time. |
| **Weekly PDF report** | Render a FinalAnswer to PDF for "email digest" Tier 3 alerts. |

## C. Things to say openly in the pitch (honesty points)
- The region-to-equity exposure map (`region_exposure.json`) is a **judgment call**, not a measured sensitivity.
- If yield labels aren't available, `stress_class` is **derived from VCI and rainfall-deficit rules**. Say so.
- The backtest uses only 6–8 events. Show the misses, and present it as a sanity check, not proof of alpha.
- Sentiment measures **tone, not a forecast**.
- Free-tier limits and model IDs change. That's why the system is local-first.
