# evals: prompt-eval harness (docs/05 §4)

```powershell
.venv\Scripts\python evals\run_evals.py                 # all prompts, local models, real calls (CACHE_MODE=off)
.venv\Scripts\python evals\run_evals.py --prompt P8 --repeat 3   # one prompt, stability check
.venv\Scripts\python evals\run_evals.py --boost         # cloud chain (spends Groq quota)
.venv\Scripts\python evals\make_fixtures.py             # regenerate the frozen evidence bundles
.venv\Scripts\python -m pytest evals\tests              # the scorers' own tests (mock mode, no GPU)
```
Run it before every prompt change. It exits 1 when any metric falls more than 10 % below the best previous run for the same prompt and model. Results go to `evals/results/<timestamp>.json`.

## What is scored
| Prompt | VALID | GROUNDED | Other |
|---|---|---|---|
| P1 intent | schema-valid after `normalise()` | – | FIELDS = field accuracy of the pipeline; `llm_only_fields` = the model alone |
| P4–P7 narrators | schema-valid `AgentSignal` | Numbers Ledger `pass` on the frozen evidence | CITED = share of figures the model cited itself; `length_ok` ≤ 3 sentences |
| P8 synthesizer | all 4 sections present (P8_hi for Hinglish cases 3, 10) | Numbers Ledger `pass` | `hedge_unchanged` (instrument and quantity copied exactly), `json_tail` parsed |
| P9 red team | schema-valid | ≥ 2 reasons cite real evidence ids, none invented | `three_reasons` |

## History on this laptop (qwen3:4b-instruct, RTX 4050, 2026-10-03)
| Run | P1 valid | P1 fields | P4 grounded / cited | P5 len ok | P7 valid | P8 grounded | P9 grounded |
|---|---|---|---|---|---|---|---|
| 1 (doc prompts) | 6/10 | 0.75 | 6/10 / 0.53 | 4/10 | 7/10 | 4/10 | 7/10 |
| 2 (prompt fixes) | 10/10 | 0.69 | 10/10 / 0.40 | 8/10 | 10/10 | 8/10 | 10/10 |
| 3 (+cite every sentence, normalise) | 10/10 | 1.00 (LLM alone 0.69) | 10/10 / 1.00 | 10/10 | 10/10 | 6/10 | 10/10 |
| final | 10/10 | 1.00 (LLM alone 0.69) | 10/10 / 0.96 | 10/10 | 10/10 | 8/10 (19/20 over 2 repeats) | 10/10 |

What changed and why:
- **P1 truncation.** The model listed every bank ticker until it hit `max_tokens`. Fixed with a bounded schema (`IntentLLM`) plus explicit rules.
- **P4 invented numbers.** It averaged confidences. The prompts now say: copy numbers exactly, give confidence in words, and cite every sentence.
- **P5 and P7 ran too long.** They now follow explicit 3-sentence structures.
- **P9 citations.** It wrote bare ids and invented `ev_missing_001`. Fixed with a cited example in the prompt and `clean_reasons()`.
- **P8 looping and invented ranges.** It looped bullets and made up per-holding ranges. Fixed with "ranges only for listed holdings" and `dedupe_lines()`.
- **Validator false flags.** Strikes inside instrument names and labels like "10-year" were flagged. The validator now handles both.

## Known limits (honest)
- **P8 grounding varies between 0.8 and 0.95 per run** at temperature 0.2. Judge P8 with `--repeat 2+`. The remaining misses are figures the model writes without a citation (e.g. "31%"), and the Numbers Ledger flags them in production too.
- **P8 takes about 11 s locally** (around 900 output tokens at 62 tok/s), above the 5 s target. Boost mode (Groq) or a shorter answer would fix that. Every other prompt runs in 2–4 s.
- **P1's 1.00 includes `normalise()` rules**, which were written against these 10 queries. The model alone scores 0.69. Add new golden cases before trusting the pipeline number on new phrasing.
- **Only `qwen3:4b-instruct` is installed here.** The narrator (gemma3) and red-team (phi4-mini) scores will differ on L2 and L3. Re-run the evals there.
