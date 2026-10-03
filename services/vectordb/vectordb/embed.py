"""Embedder: BAAI/bge-small-en-v1.5 (384-d, cosine, normalize_embeddings=True) plus the §3.3 text templates.

If the real model cannot be loaded, a deterministic hashing embedder is used and the fact is EXPOSED
(`Embedder.is_fallback`, `model_state()`): its cosines live on a different scale, so analogs.py applies its own
threshold and marks the evidence degraded instead of pretending the numbers are bge similarities.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from typing import Any

import numpy as np

from copilot_common.settings import get_settings

logger = logging.getLogger(__name__)

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIM = 384
HASHING_NAME = "hashing-384 (fallback)"

_lock = threading.Lock()
_model: Any = None
_state: dict[str, Any] = {"status": "not loaded", "device": None, "error": None}


def resolve_device(requested: str | None) -> str:
    """EMBED_DEVICE: cpu (default) | cuda | cuda:N | auto. cuda without a GPU falls back to cpu (logged)."""
    req = (requested or "cpu").strip().lower()
    if req in ("", "cpu"):
        return "cpu"
    try:
        import torch
        has_cuda = bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001
        has_cuda = False
    if req == "auto":
        return "cuda" if has_cuda else "cpu"
    if req.startswith("cuda"):
        if has_cuda:
            return req
        logger.warning("EMBED_DEVICE=%s but CUDA is not available; using cpu", requested)
        return "cpu"
    logger.warning("Unknown EMBED_DEVICE=%r; using cpu", requested)
    return "cpu"


def _load_model() -> Any:
    global _model
    if _state["status"] in ("ok", "failed"):
        return _model
    with _lock:
        if _state["status"] in ("ok", "failed"):
            return _model
        device = resolve_device(get_settings().EMBED_DEVICE)      # read OUTSIDE the try: a settings bug must surface
        try:
            from sentence_transformers import SentenceTransformer
            try:                                    # cached copy first: no hub round-trips at startup
                _model = SentenceTransformer(MODEL_NAME, device=device, local_files_only=True)
            except Exception:  # noqa: BLE001  not cached yet → download
                _model = SentenceTransformer(MODEL_NAME, device=device)
            _state.update(status="ok", device=device, error=None)
            logger.info("Loaded %s on %s", MODEL_NAME, device)
        except Exception as exc:  # noqa: BLE001  model download/load problems → honest, flagged fallback
            _model = None
            _state.update(status="failed", device=None, error=f"{type(exc).__name__}: {str(exc)[:200]}")
            logger.error("Could not load %s (%s); using the hashing fallback embedder (degraded)", MODEL_NAME, exc)
    return _model


def model_state() -> dict[str, Any]:
    return dict(_state)


def hashing_encode(texts: list[str], dim: int = DIM) -> np.ndarray:
    """Deterministic bag-of-words + bigram feature hashing, L2-normalised. Only a lexical-overlap stand-in."""
    out = np.zeros((len(texts), dim), dtype=np.float32)
    for i, text in enumerate(texts):
        words = re.findall(r"\w+", (text or "").lower())
        feats = [(w, 1.0 + 0.8 * (len(w) > 4)) for w in words]
        feats += [(f"{a}_{b}", 1.5) for a, b in zip(words, words[1:])]
        for f, wt in feats:
            h = int(hashlib.md5(f.encode("utf-8")).hexdigest(), 16)
            out[i, h % dim] += (1.0 if (h // dim) % 2 == 0 else -1.0) * wt
        n = np.linalg.norm(out[i])
        if n > 1e-9:
            out[i] /= n
    return out


class Embedder:
    """Shared embedder. Instantiating is cheap: the model is a process-wide singleton."""

    def __init__(self, load: bool = True):
        self._model = _load_model() if load else _model

    @property
    def is_fallback(self) -> bool:
        return self._model is None

    @property
    def name(self) -> str:
        return HASHING_NAME if self.is_fallback else MODEL_NAME

    def encode(self, texts: list[str], batch_size: int = 64) -> np.ndarray:
        if not texts:
            return np.zeros((0, DIM), dtype=np.float32)
        if self._model is None:
            return hashing_encode(texts)
        vecs = self._model.encode(texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vecs, dtype=np.float32)

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]


# ---------- text templates (08 §3.3; identical for corpus and query, no bge instruction prefix) ----------
def _fmt_sev(v: Any) -> Any:
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def event_text(e: dict) -> str:
    return (f"{e['event_type']} {e['region']} severity {_fmt_sev(e.get('severity_value'))} {e['severity_unit']}. "
            f"{e['description']} Mechanism: {e['mechanism']}")


def news_text(n: dict) -> str:
    return f"{n.get('title', '')}. {n.get('summary', '')}"
