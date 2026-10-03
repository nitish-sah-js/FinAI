"""Offline accuracy report for the sentiment model (07 §2, §5 step 9).

    python -m sentiment.eval --n 1000                       # kdave/Indian_Financial_News sample, seed 42
    python -m sentiment.eval --n 1000 --model Vansh180/FinBERT-India-v1
    python -m sentiment.eval --csv ../../data/handlabeled_headlines.csv   # columns: text,label

Scores every label ORDER from one forward pass ("config" = model.config.id2label, plus "alphabetical" =
negative,neutral,positive), so a fine-tune whose config kept the base model's label names is caught.
Writes services/sentiment/eval_report.json.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

LABELS = ["negative", "neutral", "positive"]
REPORT = Path(__file__).resolve().parents[1] / "eval_report.json"


def load_hf(n: int, seed: int = 42) -> list[tuple[str, str]]:
    from datasets import load_dataset
    ds = load_dataset("kdave/Indian_Financial_News")["train"]
    print(f"[eval] dataset columns: {ds.column_names}")          # inspect at runtime (05 doc rule)
    text_col = "Summary" if "Summary" in ds.column_names else ds.column_names[1]
    label_col = next(c for c in ds.column_names if "sentiment" in c.lower() or "label" in c.lower())
    idx = random.Random(seed).sample(range(len(ds)), min(n, len(ds)))
    rows = [(str(ds[i][text_col] or "")[:600], str(ds[i][label_col]).strip().lower()) for i in idx]
    return [(t, l) for t, l in rows if t and l in LABELS]


def load_csv(path: str) -> list[tuple[str, str]]:
    with open(path, encoding="utf-8") as f:
        return [(r["text"], r["label"].strip().lower()) for r in csv.DictReader(f) if r["label"].strip().lower() in LABELS]


def metrics(gold: list[str], pred: list[str]) -> dict:
    cm = {g: {p: 0 for p in LABELS} for g in LABELS}
    for g, p in zip(gold, pred):
        cm[g][p] += 1
    per = {}
    for c in LABELS:
        tp = cm[c][c]
        fp = sum(cm[g][c] for g in LABELS) - tp
        fn = sum(cm[c].values()) - tp
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        per[c] = {"precision": round(prec, 3), "recall": round(rec, 3),
                  "f1": round(2 * prec * rec / (prec + rec), 3) if prec + rec else 0.0, "support": sum(cm[c].values())}
    acc = sum(cm[c][c] for c in LABELS) / max(len(gold), 1)
    return {"accuracy": round(acc, 3), "macro_f1": round(sum(p["f1"] for p in per.values()) / 3, 3),
            "per_class": per, "confusion": cm}


def evaluate(rows: list[tuple[str, str]], model: str | None = None, batch_size: int = 32) -> dict:
    import torch
    import torch.nn.functional as F
    from .model import FinbertScorer
    sc = FinbertScorer(model)
    cfg = sc.model.config.id2label
    config_order = [cfg[k].lower() for k in sorted(cfg, key=int)]
    service_order = [sc.id2label[i] for i in sorted(sc.id2label)]
    orders = {"config": config_order, "alphabetical": LABELS, "service": service_order}
    texts, gold = [t for t, _ in rows], [l for _, l in rows]
    t0 = time.perf_counter()
    argmax: list[int] = []
    for i in range(0, len(texts), batch_size):
        enc = sc.tokenizer(texts[i:i + batch_size], truncation=True, max_length=128, padding=True,
                           return_tensors="pt").to(sc.device)
        with torch.inference_mode():
            probs = F.softmax(sc.model(**enc).logits.float(), dim=-1)
        argmax += probs.argmax(dim=-1).cpu().tolist()
    secs = time.perf_counter() - t0
    majority = Counter(gold).most_common(1)[0][0]
    out = {"model": sc.name, "device": sc.device, "n": len(rows), "seconds": round(secs, 2),
           "headlines_per_s": round(len(rows) / secs, 1) if secs else None,
           "baseline_majority": {"label": majority, **{k: v for k, v in metrics(gold, [majority] * len(gold)).items()
                                                        if k in ("accuracy", "macro_f1")}},
           "orders": {}}
    for name, order in orders.items():
        out["orders"][name] = {"order": order, **metrics(gold, [order[k] for k in argmax])}
    best = max(out["orders"], key=lambda k: out["orders"][k]["macro_f1"])
    out["best_order"] = best
    out["beats_baseline"] = out["orders"][best]["macro_f1"] > out["baseline_majority"]["macro_f1"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--model")
    ap.add_argument("--csv", help="hand-labelled CSV (text,label) instead of the HF dataset")
    ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    rows = load_csv(a.csv) if a.csv else load_hf(a.n)
    rep = evaluate(rows, a.model)
    rep["dataset"] = a.csv or "kdave/Indian_Financial_News (Summary column, seed 42)"
    print(f"\nmodel {rep['model']} on {rep['device']}: n={rep['n']} in {rep['seconds']} s ({rep['headlines_per_s']}/s)")
    print(f"majority baseline: acc {rep['baseline_majority']['accuracy']} macro-F1 {rep['baseline_majority']['macro_f1']}")
    for k, v in rep["orders"].items():
        print(f"order {k:<12} {v['order']}: acc {v['accuracy']}  macro-F1 {v['macro_f1']}")
    print(f"best order: {rep['best_order']} · beats baseline: {rep['beats_baseline']}")
    if not a.no_save:
        reports = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.is_file() else {}
        reports[f"{rep['model']}|{rep['dataset']}"] = rep
        REPORT.write_text(json.dumps(reports, indent=1), encoding="utf-8")
        print(f"saved {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
