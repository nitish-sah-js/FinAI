"""
scripts/calibrate_conformal.py
Leave-one-out conformal calibration over split=="train" events per spec §6.7.
Writes data/conformal_q.json with structure:
{
  "5d": {
    "cyclone": {"q": 0.012, "n_calib": 10},
    ...,
    "_all": {"q": 0.015, "n_calib": 32}
  },
  "1d": { ... },
  "20d": { ... }
}
"""
from __future__ import annotations
import json
import logging
import math
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np

# Add services/vectordb and packages/copilot_common to path
_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_root / "packages" / "copilot_common"))
sys.path.insert(0, str(_root / "services" / "vectordb"))

from vectordb.stats import weighted_quantile, similarity_weights

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("calibrate_conformal")


def _get_embeddings(events: list[dict]) -> np.ndarray:
    """Try to use SentenceTransformer bge-small; fallback to TF-IDF / character n-gram if torch/model unavailable."""
    texts = [e.get("embedding_text") or e.get("title", "") for e in events]
    try:
        from vectordb.embed import Embedder
        embedder = Embedder()
        vecs = embedder.encode(texts)
        return vecs
    except Exception as exc:
        logger.warning("Embedder unavailable (%s), using TF-IDF fallback for embeddings", exc)
        from sklearn.feature_extraction.text import TfidfVectorizer
        tfidf = TfidfVectorizer(max_features=384, stop_words="english")
        mat = tfidf.fit_transform(texts).toarray()
        # Normalize
        norms = np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9
        return mat / norms


def calibrate(
    events_json_path: str = "data/events.json",
    output_conformal_path: str = "data/conformal_q.json",
    k: int = 5,
    coverage: float = 0.8,
) -> dict:
    events_path = Path(events_json_path)
    if not events_path.exists():
        raise FileNotFoundError(f"Events file not found: {events_json_path}")

    with open(events_path, "r", encoding="utf-8") as f:
        all_events = json.load(f)

    # Filter to train split only
    train_events = [e for e in all_events if e.get("split") == "train"]
    n_train = len(train_events)
    logger.info("Calibrating on %d train events (out of %d total)", n_train, len(all_events))

    if n_train < 3:
        raise ValueError(f"Too few train events ({n_train}) for conformal calibration")

    vecs = _get_embeddings(train_events)  # shape (n_train, dim)

    # Precompute cosine similarities matrix: vecs . vecs.T
    sim_matrix = np.dot(vecs, vecs.T)
    # Ensure diagonal is 1.0 (self similarity)
    norms = np.linalg.norm(vecs, axis=1)
    norm_mat = np.outer(norms, norms) + 1e-9
    sim_matrix = sim_matrix / norm_mat

    horizons = ["1d", "5d", "20d"]
    results: dict[str, dict[str, dict[str, Any]]] = {h: {} for h in horizons}

    # Group train indices by event_type and collect all
    types = sorted(list({e.get("event_type", "other") for e in train_events}))

    for h in horizons:
        ret_key = f"ret_{h}"
        abn_key = f"abnormal_{h}"

        residuals_by_type: dict[str, list[float]] = {t: [] for t in types}
        all_residuals: list[float] = []

        # Leave-one-out: For each i in train_events
        for i, target_ev in enumerate(train_events):
            t_type = target_ev.get("event_type", "other")
            target_outcomes = target_ev.get("outcomes", [])
            if not target_outcomes:
                continue

            # Candidate analogs: all j != i
            cand_indices = [j for j in range(n_train) if j != i]
            cand_sims = [float(sim_matrix[i, j]) for j in cand_indices]

            # Rank candidates by similarity
            ranked_pairs = sorted(zip(cand_sims, cand_indices), key=lambda x: -x[0])[:k]
            top_sims = [p[0] for p in ranked_pairs]
            top_indices = [p[1] for p in ranked_pairs]
            top_analogs = [train_events[idx] for idx in top_indices]

            # For each outcome asset in target event, compute predicted median from analogs
            for out in target_outcomes:
                asset = out.get("asset")
                actual_val = out.get(abn_key) if out.get(abn_key) is not None else out.get(ret_key)
                if actual_val is None:
                    continue

                # Find analogs that have this asset
                analog_vals = []
                analog_weights = []
                wts = similarity_weights(top_sims)
                for analog_ev, w in zip(top_analogs, wts):
                    for a_out in analog_ev.get("outcomes", []):
                        if a_out.get("asset") == asset:
                            v = a_out.get(abn_key) if a_out.get(abn_key) is not None else a_out.get(ret_key)
                            if v is not None:
                                analog_vals.append(v)
                                analog_weights.append(w)
                            break

                if not analog_vals:
                    # Asset not in analogs: skip this asset residual
                    continue

                pred_median = weighted_quantile(analog_vals, analog_weights, 0.5)
                residual = abs(actual_val - pred_median)

                residuals_by_type[t_type].append(residual)
                all_residuals.append(residual)

        # Compute q_hat per event type
        # Formula: q̂ = quantile(residuals, ceil((n+1)·0.8)/n)
        for t_type in types:
            res_list = residuals_by_type[t_type]
            n_res = len(res_list)
            if n_res == 0:
                continue
            # Conformal quantile level
            q_level = min(1.0, math.ceil((n_res + 1) * coverage) / n_res)
            q_val = float(np.percentile(res_list, q_level * 100))
            results[h][t_type] = {
                "q": round(q_val, 6),
                "n_calib": n_res,
                "coverage_target": coverage,
            }

        # Overall across all event types for fallback
        if all_residuals:
            n_all = len(all_residuals)
            q_level = min(1.0, math.ceil((n_all + 1) * coverage) / n_all)
            q_val = float(np.percentile(all_residuals, q_level * 100))
            results[h]["_all"] = {
                "q": round(q_val, 6),
                "n_calib": n_all,
                "coverage_target": coverage,
            }

    out_p = Path(output_conformal_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    with open(out_p, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    logger.info("Saved conformal calibration to %s", output_conformal_path)
    return results


if __name__ == "__main__":
    calibrate()
