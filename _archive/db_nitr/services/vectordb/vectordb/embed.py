"""
vectordb/embed.py
Embedder using BAAI/bge-small-en-v1.5 (384-dim, cosine, normalize).
Includes fallback deterministic 384-dim vectorizer if sentence-transformers is not installed.
Also provides text template functions for events and news.
"""
from __future__ import annotations
import hashlib
import logging
import re
from typing import Any
import numpy as np

from copilot_common.settings import settings

logger = logging.getLogger(__name__)

_model: Any = None
_model_attempted: bool = False


def _get_model():
    global _model, _model_attempted
    if _model_attempted:
        return _model
    _model_attempted = True
    try:
        from sentence_transformers import SentenceTransformer
        device = settings.embed_device if settings.embed_device in ("cuda", "cpu") else "cpu"
        _model = SentenceTransformer("BAAI/bge-small-en-v1.5", device=device)
        logger.info("Loaded SentenceTransformer BAAI/bge-small-en-v1.5 on %s", device)
    except Exception as exc:
        logger.warning(
            "Could not load SentenceTransformer ('%s'). Using deterministic 384-dim fallback embedder.",
            exc,
        )
        _model = None
    return _model


def _fallback_encode(texts: list[str], dim: int = 384) -> np.ndarray:
    """
    Deterministic 384-dim vectorizer based on n-gram and word feature hashing.
    Preserves semantic similarity of overlapping words/tokens and normalizes vectors.
    """
    if not texts:
        return np.zeros((0, dim), dtype=np.float32)

    vecs = np.zeros((len(texts), dim), dtype=np.float32)
    for i, text in enumerate(texts):
        words = re.findall(r"\w+", (text or "").lower())
        if not words:
            vecs[i, 0] = 1.0
            continue
        v = np.zeros(dim, dtype=np.float32)
        for w in words:
            # Hash word into bucket
            h = int(hashlib.md5(w.encode("utf-8")).hexdigest(), 16)
            idx = h % dim
            sign = 1.0 if (h // dim) % 2 == 0 else -1.0
            weight = 1.0 + (len(w) > 4) * 0.8
            v[idx] += sign * weight

        # Also hash word pairs for sequence matching
        for j in range(len(words) - 1):
            pair = f"{words[j]}_{words[j+1]}"
            h = int(hashlib.md5(pair.encode("utf-8")).hexdigest(), 16)
            idx = h % dim
            sign = 1.0 if (h // dim) % 2 == 0 else -1.0
            v[idx] += sign * 1.5

        norm = np.linalg.norm(v)
        if norm > 1e-9:
            v = v / norm
        else:
            v[0] = 1.0
        vecs[i] = v
    return vecs


class Embedder:
    """Thin wrapper around SentenceTransformer for event and news vectors."""

    def __init__(self):
        self._model = _get_model()

    def encode(self, texts: list[str], batch_size: int = 64) -> np.ndarray:
        """
        Encode a list of texts into normalized 384-dim vectors.
        Returns ndarray of shape (n, 384), dtype float32.
        """
        if not texts:
            return np.zeros((0, 384), dtype=np.float32)

        if self._model is not None:
            vecs = self._model.encode(
                texts,
                batch_size=batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return np.array(vecs, dtype=np.float32)

        return _fallback_encode(texts, dim=384)

    def encode_one(self, text: str) -> np.ndarray:
        """Encode a single string, return shape (384,)."""
        return self.encode([text])[0]


# ---------- Text templates ----------

def event_text(e: dict) -> str:
    """
    EXACT template per spec §3.3:
    "{event_type} {region} severity {severity_value} {severity_unit}. {description} Mechanism: {mechanism}"
    """
    sev_val = e.get("severity_value")
    if isinstance(sev_val, float) and sev_val.is_integer():
        sev_val = int(sev_val)

    return (
        f"{e['event_type']} {e['region']} severity {sev_val} {e['severity_unit']}. "
        f"{e['description']} Mechanism: {e['mechanism']}"
    )


def news_text(n: dict) -> str:
    """Template for news items: "{title}. {summary}"."""
    title = n.get("title", "")
    summary = n.get("summary", "")
    return f"{title}. {summary}"
