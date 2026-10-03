# 07 — Sentiment Service (L2 :8102)

| | |
|---|---|
| **Owner / Laptop** | ML person A · **L2 "Quant & ML"** · port **8102** · folder `services/sentiment` |
| **Depends on** | `copilot_common` (01). HF model `kdave/FineTuned_Finbert`. Ollama `gemma3:4b` at `OLLAMA_L2` for the second opinion. Prompt P3 (05) |
| **Provides** | Tool `sentiment` (`POST /sentiment/score`), plus `POST /sentiment/eval` (offline accuracy report) |
| **Called by** | Orchestrator `sentiment_agent` (04). Ingestion (10) labels news before it is indexed into Weaviate (08). Monitor (11) runs sentiment-shift detection |

> **What it measures.** The model reports **tone, not a forecast**. Its output is used as one weak signal, weighted by how much of the portfolio each headline touches.

---

## 1. Folder layout
```
services/sentiment/
├── sentiment/
│   ├── main.py          # create_service_app("sentiment")
│   ├── model.py         # FinbertScorer (load once, batch, GPU/CPU)
│   ├── hinglish.py      # is_hinglish(text) heuristic
│   ├── second_opinion.py# gemma3 via OpenAI-compatible Ollama endpoint (P3 prompt)
│   ├── weighting.py     # position/relevance weighting → portfolio_sentiment
│   ├── ticker_map.py    # alias → ticker ("Reliance", "RIL" → RELIANCE.NS) from data/ticker_aliases.json
│   └── eval.py          # evaluate on kdave/Indian_Financial_News
├── tests/ test_model.py test_hinglish.py test_weighting.py
└── requirements.txt     # fastapi uvicorn transformers torch datasets httpx pydantic
data/ticker_aliases.json   # {"RELIANCE.NS":["reliance","ril","mukesh ambani"], "ITC.NS":["itc"], ...}
```

## 2. Model facts (verified)
- `kdave/FineTuned_Finbert` is fine-tuned from `yiyanghkust/finbert-tone` on Indian financial news, with **3 labels: Positive, Negative, Neutral**. Read the label order from `model.config.id2label`, **never hard-code it**.
- **Verified 2026-10-03 (see `services/sentiment/README.md`):** the weights live in the `finbert/` subfolder of the repo, and the config's `id2label` is WRONG for this fine-tune. The true order is alphabetical (negative, neutral, positive): accuracy is 0.098 with the config order and 0.775 with alphabetical. `model.py` keeps a per-model override; `eval.py` checks both orders.
- Its training labels were GPT-generated, so treat its accuracy as approximate. That is why we evaluate it ourselves (§5 `eval.py`).
- Alternative model: `Vansh180/FinBERT-India-v1`. Switch with env `SENTIMENT_MODEL=...`, and pick whichever scores better in `eval.py`.
- Evaluation dataset: `kdave/Indian_Financial_News` (HF datasets). Use a held-out sample of about 1,000 rows. If the model was trained on this dataset its scores may be optimistic, so also hand-label 50 recent headlines for an honest check.
- VRAM is about 0.5 GB on GPU. It also runs on CPU at roughly 20–60 headlines/s with batch size 32 (measure this yourself).

## 3. Logic
1. **Normalize and dedupe.** Strip HTML, collapse whitespace, and hash `title.lower()` to drop duplicates.
2. **FinBERT batch.** Score `title + ". " + summary[:300]`. Apply softmax. `label` = argmax, `confidence` = max prob, and `score` = `P(pos) − P(neg)` in [−1, 1].
3. **Hinglish heuristic** (`is_hinglish`). Return true if any of these hold:
   - The text contains Devanagari characters (`ऀ-ॿ`).
   - The share of tokens found in a Roman-Hindi wordlist is ≥ 0.15. Use about 200 words (`hai, nahi, kya, bhi, mein, ke, ki, ka, aur, gaya, raha, sasta, mehenga, girawat, tezi, bazaar, ...`).
   - Detection with `langid` or `fasttext lid.176` reports `hi`.
4. **Second opinion** if `confidence < 0.6` or Hinglish. Call gemma3:4b with P3 (05) through `OpenAI(base_url=OLLAMA_L2+"/v1")`, using JSON mode, `temperature=0`, `think` off, a 10 s timeout and at most 8 concurrent calls.
   - **Merge.** If the labels agree, keep FinBERT's label and set `confidence=max`. If they disagree, use the LLM label when its confidence ≥ 0.7, otherwise `neutral` with `confidence=0.4`. Store the LLM result in `second_opinion`.
5. **Ticker linking.** Use explicit `tickers` from ingestion, otherwise alias matching. `relevance` = 1.0 for a direct company mention, 0.5 for a sector keyword (from `sector_sensitivity.json` sectors) and 0.2 for macro-only.
6. **Weighting.** `weight = position_weight(ticker) × relevance × recency`, where `recency = exp(−age_hours/24)`.
   - `by_ticker[T] = Σ weight·score / Σ weight` over the items for T.
   - `portfolio_sentiment = Σ_T w_T · by_ticker[T]`, where `w_T` is the portfolio weight. If no portfolio is given, use an equal-weight mean.
7. **Evidence confidence** = mean item confidence × min(1, n_items/10). Fewer than 3 items caps confidence at 0.3, with the warning `"few headlines"`.

## 4. Request / response
```python
class NewsIn(BaseModel):
    news_id: str; title: str; summary: str = ""; source: str = ""
    published_at: datetime; tickers: list[str] = []
class ScoreReq(BaseModel):
    items: list[NewsIn]
    portfolio: Portfolio | None = None
    second_opinion: bool = True
    as_of: date | None = None; run_id: str | None = None
```

### Example `POST /sentiment/score`
```json
{"items":[
  {"news_id":"n_8f21","title":"Reliance shares slip as crude rally squeezes refining margins","summary":"","source":"Economic Times","published_at":"2026-10-03T05:10:00Z","tickers":["RELIANCE.NS"]},
  {"news_id":"n_8f22","title":"ITC ka FMCG business mazboot, lekin cigarette volume par dabav","source":"Moneycontrol Hindi","published_at":"2026-10-03T04:00:00Z","tickers":["ITC.NS"]}],
 "portfolio":{"holdings":[{"ticker":"RELIANCE.NS","qty":100},{"ticker":"ITC.NS","qty":400}]}}
```
```json
{"evidence":[{"id":"ev_sentiment_003","run_id":"run_20261003141502_a91f","tool":"sentiment",
  "value":{
    "items":[
      {"news_id":"n_8f21","label":"negative","score":-0.71,"confidence":0.86,"model":"kdave/FineTuned_Finbert",
       "second_opinion":null,"weight":0.33,"relevance":1.0,"hinglish":false},
      {"news_id":"n_8f22","label":"neutral","score":-0.08,"confidence":0.52,"model":"kdave/FineTuned_Finbert",
       "second_opinion":{"sentiment":"negative","confidence":0.64,"affected_tickers":["ITC.NS"],"materiality":"medium","horizon":"weeks","model":"gemma3:4b"},
       "weight":0.31,"relevance":1.0,"hinglish":true}],
    "portfolio_sentiment":-0.36,
    "by_ticker":{"RELIANCE.NS":-0.71,"ITC.NS":-0.08}},
  "summary":"Portfolio news tone −0.36 (2 headlines; 1 Hinglish sent to second opinion)",
  "source":"FinBERT (kdave/FineTuned_Finbert) + gemma3:4b second opinion","as_of":"2026-10-03T05:10:00Z",
  "timestamp":"2026-10-03T08:45:05Z","freshness_s":12900,"confidence":0.3,"degraded":false,"latency_ms":910,
  "model_version":"finbert_kdave+gemma3_p3v1"}],
 "warnings":["few headlines","label disagreement on n_8f22 → neutral (LLM conf 0.64 < 0.7)"]}
```
Labels are emitted **lower-case** (`positive|neutral|negative`) to match P3 (05).

## 5. Build prompt (paste into a coding LLM)
```
Build a FastAPI service "sentiment" (port 8102) using copilot_common (Evidence, ToolResult, Portfolio,
create_service_app, mock_or, cached, EvidenceCounter, settings).

Step 1. requirements.txt: fastapi uvicorn transformers torch datasets httpx openai pydantic langid.
Step 2. sentiment/model.py: class FinbertScorer:
          __init__(name=os.getenv("SENTIMENT_MODEL","kdave/FineTuned_Finbert")) — load tokenizer+model ONCE,
            device = "cuda" if torch.cuda.is_available() else "cpu", model.eval(), fp16 on cuda.
            Build label map from model.config.id2label lower-cased (do NOT hard-code order).
          score(texts: list[str], batch_size=32) -> list[dict(label, score=P(pos)-P(neg), confidence=max prob, probs)]
            torch.inference_mode(), truncation max_length=128.
        Called from endpoints via asyncio.to_thread.
Step 3. sentiment/hinglish.py: is_hinglish(text) -> bool with Devanagari check, Roman-Hindi wordlist ratio
        >= 0.15 (include a 200-word list in the file), and langid fallback.
Step 4. sentiment/second_opinion.py: async ask_gemma(headline) -> dict|None using openai.AsyncOpenAI(
        base_url=settings.OLLAMA_L2+"/v1", api_key="ollama"), model "gemma3:4b", temperature 0,
        response_format={"type":"json_object"}, extra_body={"think": False}, timeout 10s, semaphore(8).
        System prompt = P3 from 05_RUNTIME_PROMPTS.md (pasted below). Validate JSON keys; return None on failure.
        Cache with cached("sentiment", {"h":headline,"m":"gemma3:4b","p":"P3v1"}, fn).
Step 5. merge(finbert, llm) rule exactly as spec §3.4.
Step 6. sentiment/ticker_map.py: link tickers via data/ticker_aliases.json; relevance 1.0/0.5/0.2.
Step 7. sentiment/weighting.py: weight = position_weight × relevance × exp(-age_h/24);
        by_ticker and portfolio_sentiment as spec §3.6. Position weights from portfolio qty×avg_price
        (if avg_price missing, use equal weights and add warning).
Step 8. main.py: POST /sentiment/score (ScoreReq) -> ToolResult with ONE Evidence (tool="sentiment"),
        value schema exactly as example; confidence rule §3.7. Dedupe items by title hash.
        POST /sentiment/eval {"n":1000,"model":...} -> runs eval.py and returns accuracy, macro-F1,
        confusion matrix, per-class report (as Evidence tool="sentiment", value.eval=...).
        mock_or wraps both.
Step 9. eval.py: load_dataset("kdave/Indian_Financial_News"), inspect column names at runtime
        (print them), map labels to lower-case, sample n with seed 42, score, report accuracy & macro-F1 and
        a majority-class baseline. Also support --csv data/handlabeled_50.csv (columns text,label).
Step 10. tests: label map built from id2label; is_hinglish true for "Sensex aaj gir gaya" and Devanagari, false
        for an English headline; weighting sums; MOCK=1 returns fixture; merge rule table-driven test.
Output all files completely.
[PASTE §2–§4 OF 07_SENTIMENT_SERVICE.md, P3 FROM 05, §5–7 OF 01_CONTRACTS.md]
```

## 6. Mock mode
With `MOCK=1`, `/sentiment/score` returns `fixtures/sentiment/score.json` (the example above) with `degraded_reason="mock"`. The FinBERT model is not loaded at all, so startup takes under 1 s.

## 7. Acceptance checklist
Status on L1 (2026-10-03): 100 headlines in 0.04 s on GPU ✓ · eval report saved, macro-F1 0.776 vs baseline 0.168 ✓ ·
gemma3 down → FinBERT kept + warning ✓ · validates as ToolResult, lower-case labels ✓ · second run served from cache ✓
(CPU fallback: set `SENTIMENT_DEVICE=cpu`; not timed yet).

- [ ] 100 headlines score in under 3 s on the L2 GPU, and the CPU fallback works when `CUDA_VISIBLE_DEVICES=""`
- [ ] The `eval.py` report is saved to `services/sentiment/eval_report.json`, and FinBERT beats the majority baseline on macro-F1
- [ ] Hinglish and low-confidence items go to gemma3. With gemma3 down, the FinBERT result is kept and a warning is added
- [ ] Output validates as `ToolResult`, with labels in lower case
- [ ] Scoring the same items twice hits the cache on the second run

## 8. Integration hooks
- **Orchestrator `sentiment_agent` (04).** Gets news from ingestion `POST /news` (by tickers from Intent and the portfolio, last 48 h), calls `/sentiment/score` with the portfolio, then sends the evidence to the narrator. Sentiment has no separate narrator; the synthesizer uses `summary` directly.
- **Ingestion (10).** Calls `/sentiment/score` with `second_opinion=false` for speed on each RSS batch, then `vectordb /news/index` (08) with `sentiment_label` and `sentiment_score`.
- **Monitor (11).** The sentiment-shift detector compares `by_ticker` over the last 6 h against the 7-day mean.
- **Terminal (13).** Evidence drill-down shows each headline with its label chip, plus a 🔁 icon when a second opinion was used.

## 9. Sources
- https://huggingface.co/kdave/FineTuned_Finbert
- https://huggingface.co/datasets/kdave/Indian_Financial_News
- https://huggingface.co/Vansh180/FinBERT-India-v1
- https://docs.ollama.com/capabilities/thinking (`think: false`)
