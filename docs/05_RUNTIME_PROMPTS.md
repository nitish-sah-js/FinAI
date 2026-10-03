# 05 — Runtime Prompts (P1–P11) + Prompt-Eval Harness

| | |
|---|---|
| **Owner / Laptop** | One "prompt owner" (any laptop). Prompts are plain text files, so editing them blocks nobody. **The files in `services/orchestrator/prompts/` are the source of truth;** they were tuned with the evals and are stricter than the drafts below |
| **Depends on** | 01 (schemas), 03 (roles → models) |
| **Provides** | `services/orchestrator/prompts/P1.txt … P11.txt`, `P8_hi.txt`, `ground_rules.txt`, `evals/golden.jsonl`, `evals/run_evals.py` |
| **Build when** | Draft at H1–H4, then tune with evals from H8 onwards |

Rules for writing prompts for 1.7B–4B models: keep them short, use imperative sentences, show the exact JSON shape, give one example, and add no persona fluff. The planner never decides the plan (the router does), so prompts only parse, narrate, synthesize or critique.

---

## 1. Folder layout
```
services/orchestrator/prompts/
├── ground_rules.txt   P1.txt … P11.txt   P8_hi.txt
evals/
├── golden.jsonl       # 10 golden queries with expected properties
├── fixtures/          # frozen evidence bundles for P4-P9 evals
└── run_evals.py       # scores every prompt, prints a table, exits 1 on regression
```

Template variables use `{curly}` and are filled by the orchestrator. `{evidence_json}` is always a **compact** JSON list of the Evidence objects relevant to that prompt. Only the `id, tool, value, confidence, degraded, as_of` fields are included, to save tokens; this matters for Groq's tokens-per-minute limit and the 8K Cerebras context.

## 2. Shared ground rules (`ground_rules.txt`, prepended to P4–P10)
```
You are a component in a financial decision-support system for Indian-market investors. Rules:
1. Use ONLY numbers present in EVIDENCE. Never invent, estimate, or recall figures.
2. Cite every number with its evidence id in square brackets, e.g. [ev_weather_001].
3. If evidence is missing, degraded=true, or confidence < 0.4, say so plainly.
4. Output only the requested format. No preamble, no closing remarks.
5. This is decision support, not a trading signal.
```

---

## 3. Prompts

### P1 — Intent parser
**Model and settings:** role `intent` → `qwen3:4b-instruct`, temp 0, JSON schema `Intent` (01 §5), think off.
```
Parse the user's query about markets. Return JSON only, matching:
{"intent":"event_impact|portfolio_risk|hedge_request|explain|market_summary",
 "event_type":"hurricane|cyclone|monsoon|heatwave|rates|oil|policy|other|null",
 "region": string|null, "tickers": [Yahoo tickers, NSE with .NS], "asset_classes": [string],
 "horizon_days": int (default 5), "references_portfolio": bool,
 "needs_tools": subset of ["sentiment","weather","agri","macro","analogs","exposure","risk","hedge"]}
Rules: "my portfolio/holdings/stocks" → references_portfolio=true. "this week"=5, "today"=1,
"this month"=20. Map company names to tickers (Reliance→RELIANCE.NS, ITC→ITC.NS, Nifty→^NSEI).
Query: {query}
```
Example: query `"Agar monsoon kamzor raha toh mere FMCG stocks ka kya hoga next month?"` →
```json
{"intent":"event_impact","event_type":"monsoon","region":"India","tickers":[],"asset_classes":["equity"],
 "horizon_days":20,"references_portfolio":true,"needs_tools":["weather","agri","analogs","exposure","sentiment"]}
```
Fallback if the output is invalid twice: keyword rules in `parse_intent.py` (cyclone, monsoon, hedge, VaR and similar).

### P2 — Planner / extra-tool selector (optional)
**Model:** role `planner` → `qwen3:4b-instruct` locally, or Groq `qwen3-32b` in boost mode. Temp 0, with the function-calling `tools` taken from the tool-input schemas.
```
The following tools have ALREADY run: {done_tools}. Their evidence summaries: {evidence_summaries}.
User query: {query}
You may call at most 2 additional tools from the provided list ONLY if the evidence does not cover
the query. If coverage is sufficient, call no tool and reply "SUFFICIENT".
```
Example output: one tool call `event_study({"ticker":"ONGC.NS","event_date":"2021-08-29","window":[-1,5]})`, or the text `SUFFICIENT`.

### P3 — Sentiment second opinion (runs inside the sentiment service, 07)
**Model:** role `sentiment2` → `gemma3:4b`, temp 0. Triggered when FinBERT confidence is below 0.6 or the text is Hinglish/Hindi.
```
Classify this headline for an Indian-market investor. Return JSON only:
{"sentiment":"positive|neutral|negative","confidence":0-1,"affected_tickers":[".NS tickers"],
 "materiality":"low|medium|high","horizon":"intraday|days|weeks"}
Translate Hinglish/Hindi internally. Judge the impact on listed Indian companies, not just the tone.
Headline: {headline}
```
Example: `"Adani Ports pe cyclone ka asar, Mundra operations 2 din band"` →
```json
{"sentiment":"negative","confidence":0.78,"affected_tickers":["ADANIPORTS.NS"],"materiality":"medium","horizon":"days"}
```

### P4 — Weather narrator
**Model:** role `narrator` → `gemma3:4b`, temp 0.2, schema `AgentSignal`.
```
{ground_rules}
EVIDENCE: {evidence_json}
EXPOSED HOLDINGS: {exposed_holdings}     # [{"ticker","weight","weather_sens"}]
Write at most 3 sentences: what is happening, which holdings or sectors are exposed, and the confidence.
Copy numbers exactly; never compute or average them. End every sentence with its [ev_...] id. Give confidence in words.
Return JSON: {"agent":"weather_agent","signal":"bullish|bearish|neutral|mixed","summary":str,"evidence_ids":[str]}
```
Example output:
```json
{"agent":"weather_agent","signal":"bearish","summary":"A Category-2-equivalent severe cyclonic storm is tracking toward the Odisha coast with 180 mm rain forecast [ev_weather_001]. Exposed: COALINDIA.NS and NTPC.NS (31% weight [ev_exposure_001]). Weather data is 6 hours old and marked degraded [ev_weather_001].","evidence_ids":["ev_weather_001","ev_exposure_001"],"confidence":0.55}
```

### P5 — Agri narrator
Same model and format as P4, with `agent:"agri_agent"`. Input: the agri evidence (09 output). Extra lines:
```
Mention the yield range (q10 to q90), not just the median. State that crop stress is a slow
signal (weeks to months), not an intraday one.
```
Example summary: `"NDVI is 1.4σ below normal in Yavatmal [ev_agri_001]; the model classes it 'stressed' with a yield anomaly of -18% to -1% (median -9.5%) [ev_agri_001]. This is a slow signal for agri-input and FMCG names over weeks, not days."`

### P6 — Macro narrator
Same format, with `agent:"macro_agent"`. Extra line:
```
Link each move (repo rate, CPI, USD/INR, Brent, US 10y) to the holdings' sensitivity: banks to rates,
OMCs/paints/aviation to crude, IT to USD/INR. Skip factors that did not move.
```

### P7 — Analog explainer
Same format, with `agent:"analog_agent"`. Input: `find_analogs` evidence (08).
```
For each of the top 3 analogs write one sentence on why it is similar and one on how it differs.
Then report the outcome RANGE from the distribution list (one entry per asset; use each entry's p10 to p90,
and conformal_lo to conformal_hi if present) for the stated asset and horizon. Never give a single-number forecast. If n < 3, say the
confidence is low.
```

### P8 — Synthesizer (English)
**Model:** role `synthesizer` → local `qwen3:4b-instruct`, or the boost chain (Groq gpt-oss-120b, then others). Temp 0.2. Output is markdown followed by a JSON tail.
```
{ground_rules}
USER QUERY: {query}
PORTFOLIO: {portfolio_summary}
AGENT SIGNALS: {signals_json}
ANALOG RANGES (pre-written, cited): {distribution_json}
RISK (pre-written, cited): {risk_json}
HEDGES (computed; copy each line exactly as one bullet, never change a number): {hedges_json}
Copy figures together with their [ev_...] tag. Never write raw JSON in the answer.
Write the answer in markdown with exactly these sections:
### Bottom line        (2 sentences)
### Impact on your holdings   (bullet per affected holding, with ranges and citations)
### Suggested hedges   (exactly as computed; do not resize or add any)
### Confidence and what could be wrong   (confidence low/medium/high + 2-3 bullets)
_Decision support, not a trading signal._
Then on a new line output: <json>{"confidence":"low|medium|high","holdings_impact":[{"ticker","impact","range","evidence_ids"}],"what_could_be_wrong":[str]}</json>
```
The orchestrator splits the markdown from the `<json>` tail (also a bare trailing JSON object: 4B models often drop the tags). If the tail is invalid, it derives the fields in code.

**Why the analog, risk and hedge inputs are pre-written sentences (changed after testing with the real 4B model):** given raw JSON, `qwen3:4b-instruct` pasted JSON into the answer and wrote `p10=-0.041` without citations. The orchestrator now passes finished, cited lines (built by `analog_lines`, `risk_lines` and `hedge_lines` in `nodes/synthesizer.py`), e.g. `Sell 1 lot NIFTY OCT FUT short (hedge ratio 0.42, est. cost ₹1,850) [ev_hedge_001].`, so the model only has to copy them.

### P8_hi — Synthesizer (Hindi / Hinglish), selected by `QueryRequest.lang`
Same inputs. Replace the writing instructions with:
```
Write the answer in {lang_name} (Hindi in Devanagari for "hi"; Roman-script Hinglish for "hinglish").
Keep tickers, numbers, and [ev_...] citations exactly as in the evidence (do not translate or round them).
Section headings: ### Saar (Bottom line) · ### Aapke holdings par asar · ### Hedge sujhav · ### Bharosa aur jokhim
End with: _Yeh nirnay-sahayata hai, trading signal nahi._
```
Model choice: in boost mode use the cloud chain. Locally, prefer `gemma3:4b` (better at Hindi) over qwen3:4b-instruct. In the routing table this is the `narrator` provider with the synthesizer's token budget.

### P9 — Red Team
**Model:** role `red_team` → `phi4-mini` (L3), temp 0.3, schema `RedTeamReport`.
```
{ground_rules}
You are the Red Team. RECOMMENDATION: {draft}
EVIDENCE: {evidence_json}
List the 3 strongest reasons this could be wrong (stale data, weak analog match, low-confidence or
degraded signals, crowded trade, correlation breakdown, small sample). Cite evidence ids.
Return JSON: {"reasons":[str,str,str],"verdict":"proceed|proceed with caution|do not act","verdict_reason":str}
```
Example:
```json
{"reasons":["Only 4 analogs, with p10–p90 spanning zero [ev_analogs_001]","Weather evidence is degraded (cache from 6h ago) [ev_weather_001]","Hedge assumes beta 0.82 holds in stress; correlations rose in past cyclones [ev_risk_001]"],
 "verdict":"proceed with caution","verdict_reason":"Direction is plausible but the magnitude is poorly constrained."}
```

### P10 — Explain yourself
**Model:** role `explain` → `qwen3:4b-instruct`, temp 0.
```
{ground_rules}
RUN LOG (ordered events and evidence): {log_json}
Explain, in order, which tools ran, what each returned (one line each, with citations), and which
evidence supported each part of the final recommendation. If a step was degraded or skipped, say so.
Do not add anything that is not in the log. Use a numbered list.
```

### P11 — Alert writer (monitor, 11)
**Model:** role `alert` → `qwen3:1.7b`, temp 0.2, plain text.
```
Write ONE sentence under 25 words: what happened, which holding, why it matters, and the confidence.
FACTS: {alert_facts_json}
```
Example: `"Cyclone track shifted toward Paradip; NTPC and Coal India exposed (31% of portfolio); confidence medium."`
If the output has more than 25 words, the code truncates it to the first sentence. If the model fails, a template is used.

---

## 4. Prompt-eval harness (`evals/run_evals.py`)

`golden.jsonl` has one line per case. P1 runs on the query. For P4–P9, frozen evidence comes from `evals/fixtures/<case>.json`.

| # | Query | Expected checks |
|---|---|---|
| 1 | "Hurricane in the Gulf heading to Louisiana — impact on my energy stocks this week?" | intent=event_impact, event_type=hurricane, horizon 5, analogs in needs_tools |
| 2 | "Cyclone heading to Odisha — what happens to my portfolio?" | cyclone, region contains Odisha, references_portfolio |
| 3 | "Agar monsoon kamzor raha toh FMCG ka kya hoga next month?" (Hinglish) | monsoon, horizon 20, agri in tools |
| 4 | "What's my 1-day VaR?" | portfolio_risk, horizon 1, risk in tools |
| 5 | "Hedge my Reliance and ONGC position against an oil crash" | hedge_request, tickers include RELIANCE.NS and ONGC.NS, event_type oil |
| 6 | "RBI hiked repo by 50bps, which of my banks get hurt?" | event_type rates, macro in tools |
| 7 | "Heatwave in north India — power and cooling stocks?" | heatwave |
| 8 | "Explain your last recommendation" | intent=explain |
| 9 | "Summarise the market today" | market_summary, horizon 1 |
| 10 | "ITC ke liye kharif crop stress kitna bura hai?" | agri, tickers include ITC.NS |

**Scored checks:**
- P1: field-level accuracy against the expected values.
- P4–P8:
  - JSON or format validity.
  - **Number grounding**: the 04 validator runs on the output and must give `pass`.
  - Citation coverage: at least 1 citation per number.
  - Length limits: P4–P7 at most 3 sentences, P11 at most 25 words.
- P8: required sections are present, and hedges are not resized (quantities equal the input).
- P9: exactly 3 reasons and a valid verdict.

Output:
```
PROMPT  MODEL        CASES  VALID  GROUNDED  FIELDS  AVG_MS
P1      qwen3:4b-instruct     10     10/10  -         0.93    620
P4      gemma3:4b    10     10/10  9/10      -       1450
P8      qwen3:4b-instruct     10     9/10   9/10      -       3900
P8      groq_oss120b 10     10/10  10/10     -       1100
P9      phi4-mini    10     10/10  8/10      -       1900
```
Saves `evals/results/<timestamp>.json`. Exits 1 if any metric falls more than 10% below the previous best, so it can run before every prompt commit. Evals use `CACHE_MODE=off` so the results are real, and `LLM_MODE=local` unless `--boost` is given (to save Groq quota).

**Built:** `evals/golden.jsonl`, `evals/make_fixtures.py` → `evals/fixtures/case_01..10.json` (frozen, case-specific evidence), `evals/run_evals.py`, `evals/tests/`. Also scores a `P1-rules` row (the keyword fallback in `parse_intent.py`) as a baseline. Note that those rules were written against these same 10 queries, so their score is optimistic.

## 5. Build prompt (paste into a coding LLM)
```
Create the prompt files and eval harness for an orchestrator.
Step 1. Write services/orchestrator/prompts/ground_rules.txt, P1.txt … P11.txt, P8_hi.txt with
        EXACTLY the texts in the spec (keep {placeholders}).
Step 2. Write services/orchestrator/prompt_loader.py: load(name) -> str; render(name, **vars) that
        prepends ground_rules for P4-P10, json-dumps dict/list vars compactly (separators=(",",":")),
        and raises KeyError on a missing placeholder.
Step 3. Write evals/golden.jsonl with the 10 cases (fields: id, query, lang, expect:{...}) and
        evals/fixtures/case_XX.json (a ToolResult evidence bundle per case, realistic numbers).
Step 4. Write evals/run_evals.py: for each case run P1 via copilot_llm.llm.chat("intent", schema=Intent),
        and P4..P9 on the fixture evidence; compute the metrics in spec §4 (import the validator from
        services/orchestrator/nodes/validator.py); print the table; save results; regression exit code.
        CLI flags: --prompt P4 (only one), --boost, --repeat N (stability check).
Output complete files.
[PASTE THIS WHOLE FILE]
```

## 6. Mock mode
`copilot_common/fixtures/llm/<role>.json` holds one canned output per role (the examples above), so `MOCK=1` runs without models. Keep the fixtures in sync with these examples.

## 7. Acceptance checklist
Status on L1 (qwen3:4b-instruct, 2026-10-03; details in `evals/README.md`):
P1 fields 1.00 with `normalise()` (LLM alone 0.69) ✓ · P4–P7 grounded 10/10 each ✓ · P8 grounded 8/10 (19/20 over 2 repeats) ~ ·
P8 hedges unchanged 10/10 ✓ · Hinglish P1 and P8_hi valid ✓ · under 5 s: every prompt except P8 (~11 s locally) ✗ for P8.

- [ ] P1 field accuracy ≥ 0.85 on the golden set with qwen3:4b-instruct
- [ ] P4–P8 grounded on ≥ 9/10 (validator `pass`)
- [ ] P8 never changes hedge quantities
- [ ] The Hinglish case (3) and the Hindi output (P8_hi) both produce valid output
- [ ] Each local prompt runs in under 5 s on L1/L2 (record the timings)

## 8. Integration hooks
- The orchestrator (04) is the only consumer of P1, P2 and P4–P10. The sentiment service (07) uses P3, and the monitor (11) uses P11. Copy P3 and P11 into those services or import `prompt_loader`.
- If you change a schema, change it in 01 first, then update the JSON shapes in the prompts here.

## 9. Sources
- Ollama structured outputs: https://docs.ollama.com/capabilities/structured-outputs
- Qwen3 thinking toggle: https://docs.ollama.com/capabilities/thinking
- Groq tool use: https://console.groq.com/docs/tool-use
