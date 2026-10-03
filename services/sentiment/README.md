# Sentiment service: FinBERT-India on L2:8102 (docs/07)

```powershell
# once (repo root): GPU torch, then the service deps
.venv\Scripts\python -m pip install torch --index-url https://download.pytorch.org/whl/cu130
.venv\Scripts\python -m pip install -r services/sentiment/requirements.txt

cd services\sentiment
..\..\.venv\Scripts\python -m uvicorn sentiment.main:app --host 0.0.0.0 --port 8102
..\..\.venv\Scripts\python -m pytest tests -q                    # 67 tests, no model download needed
..\..\.venv\Scripts\python -m sentiment.eval --n 1000            # accuracy report → eval_report.json
```
`POST /sentiment/score` returns a contract `ToolResult` (01). `POST /sentiment/eval` runs the accuracy report. `GET /health` is the shared Health model: it reports `degraded` with the reason when gemma3 is missing or FinBERT failed to load. `MOCK=1` returns the shared fixture and does not load the model.

## Measured on L1 (RTX 4050), 2026-10-03
| | |
|---|---|
| Speed | 100 headlines in 0.04 s on GPU (~730/s in eval batches), 0.49 GB VRAM |
| Accuracy, kdave/Indian_Financial_News, 1,000 rows | **0.775** (macro-F1 0.776) vs majority baseline 0.338 / 0.168 |
| Accuracy, 30 hand-labelled headlines (`data/handlabeled_headlines.csv`) | **0.90** |
| Alternative `Vansh180/FinBERT-India-v1` | 0.48 / 0.83, so kdave stays the default |
| Second opinion | via `copilot_llm` role `sentiment2` (gemma3 on L2, falls back to qwen3 on L1); repeat calls are served from the cache (11.3 s → 0.24 s) |

## Bugs found and fixed while integrating
1. **Labels were inverted.** `kdave/FineTuned_Finbert`'s `config.id2label` (Neutral, Positive, Negative) is wrong for the fine-tuned weights. With it, accuracy was **0.098**: "HDFC Bank profit rises 18%" came out −0.94. The real order is alphabetical; see `KNOWN_LABEL_ORDER` in `model.py` and the `config` vs `service` rows in `eval_report.json`.
2. **The model could not load.** The hub repo keeps the weights in `finbert/` (`subfolder="finbert"`).
3. **GPU device mismatch.** The model was never moved to CUDA while the inputs were.
4. **`test_model.py` patched `AutoTokenizer`** but the code used `BertTokenizer`, so 4 tests failed.
5. **Hinglish false positives.** "the", "main", "share" and "nifty" counted as Hindi, so plain English headlines went to the slow second opinion.
6. **Ticker linking.** "heavy rain" linked to RAIN.NS and "Odisha coast" to BLUECOAST.NS. One-word names now need a capital letter, symbols need upper case, and partial matches need 2 shared tokens.
7. **Second opinion bypassed the gateway.** It called Ollama with `think: False` (gemma3 rejects that) and used its own prompt. It now uses P3 from doc 05 (kept in sync by a test).
8. **An LLM label override kept FinBERT's score.** A headline labelled negative could push the portfolio up. The score now follows the label.
9. **Contract gaps.** The evidence lacked `source`, `as_of`, `freshness_s` and the `ev_sentiment_NNN` id. `avg_price=None` crashed the weighting. There was no `as_of` filter, no HTML stripping, and no warning when the second opinion is down.
10. **Data path.** It read and wrote `services/data/`. It now uses the repo `data/` (`nse_symbols.json`, `hindi_words.json`, `ticker_aliases.json`). A stale NSE cache is used when the refresh fails.
