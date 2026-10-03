"""FinBERT scorer: loaded once, batched, GPU (fp16) or CPU (07 §2-3)."""
import asyncio
import os
import time

import torch
import torch.nn.functional as F
from transformers import AutoModelForSequenceClassification, AutoTokenizer

# Some hub repos keep the weights in a subfolder: kdave/FineTuned_Finbert ships them under finbert/ (verified on the
# hub; loading the repo root fails with "no file named pytorch_model.bin"). Override with SENTIMENT_SUBFOLDER.
KNOWN_SUBFOLDERS = {"kdave/FineTuned_Finbert": "finbert"}

# Models whose config.id2label is WRONG. kdave/FineTuned_Finbert keeps the base model's names
# (Neutral, Positive, Negative) but was fine-tuned with alphabetical ids. Measured with sentiment/eval.py (2026-10-03):
#   config order       → accuracy 0.098 on 1,000 kdave/Indian_Financial_News rows, 0.067 on 30 hand-labelled headlines
#   alphabetical order → accuracy 0.775 (macro-F1 0.776) and 0.90
# Override with SENTIMENT_LABEL_ORDER="negative,neutral,positive". Every other model uses config.id2label.
KNOWN_LABEL_ORDER = {"kdave/FineTuned_Finbert": ["negative", "neutral", "positive"]}


class FinbertScorer:
    def __init__(self, name: str | None = None):
        self.name = name or os.getenv("SENTIMENT_MODEL", "kdave/FineTuned_Finbert")
        sub = os.getenv("SENTIMENT_SUBFOLDER", KNOWN_SUBFOLDERS.get(self.name, ""))
        kw = {"subfolder": sub} if sub else {}
        self.device = "cuda" if torch.cuda.is_available() and os.getenv("SENTIMENT_DEVICE", "") != "cpu" else "cpu"
        print(f"[FinBERT] Loading {self.name}{'/' + sub if sub else ''} on {self.device}")
        t0 = time.perf_counter()
        self.tokenizer = AutoTokenizer.from_pretrained(self.name, **kw)
        model = AutoModelForSequenceClassification.from_pretrained(self.name, **kw)
        model = model.to(self.device)          # the inputs go to self.device, so the weights must too
        if self.device == "cuda":
            model = model.half()
        model.eval()
        self.model = model
        self.load_s = round(time.perf_counter() - t0, 1)

        # label order from config, unless the model is known to ship a wrong config (see KNOWN_LABEL_ORDER)
        order = os.getenv("SENTIMENT_LABEL_ORDER", "")
        fixed = [x.strip().lower() for x in order.split(",") if x.strip()] or KNOWN_LABEL_ORDER.get(self.name)
        if fixed:
            self.id2label = dict(enumerate(fixed))
            self.label_source = "override"
        else:
            self.id2label = {int(k): v.lower() for k, v in self.model.config.id2label.items()}
            self.label_source = "config"
        self._pos_idx = next(k for k, v in self.id2label.items() if v == "positive")
        self._neg_idx = next(k for k, v in self.id2label.items() if v == "negative")
        print(f"[FinBERT] Ready in {self.load_s}s. Labels: {self.id2label}")

    def score(self, texts: list[str], batch_size: int = 32) -> list[dict]:
        results = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i: i + batch_size]
            enc = self.tokenizer(
                batch,
                truncation=True,
                max_length=128,
                padding=True,
                return_tensors="pt",
            ).to(self.device)
            with torch.inference_mode():
                logits = self.model(**enc).logits
            probs = F.softmax(logits.float(), dim=-1).cpu().tolist()
            for p in probs:
                label_idx = int(p.index(max(p)))
                results.append({
                    "label":      self.id2label[label_idx],
                    "score":      round(p[self._pos_idx] - p[self._neg_idx], 4),
                    "confidence": round(max(p), 4),
                    "probs":      {self.id2label[k]: round(v, 4)
                                   for k, v in enumerate(p)},
                })
        return results


_scorer: FinbertScorer | None = None


def get_scorer() -> FinbertScorer:
    global _scorer
    if _scorer is None:
        _scorer = FinbertScorer()
    return _scorer


async def score_async(texts: list[str]) -> list[dict]:
    scorer = get_scorer()
    return await asyncio.to_thread(scorer.score, texts)
