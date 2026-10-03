"""Load the §7 artifacts and predict. Feature order and class order come ONLY from metadata.json.

LightGBM's predict_proba silently accepts a DataFrame whose columns are in the wrong order, so every input is
re-indexed to metadata["classifier_features"] here, and the model's own feature names are checked at load time
(the service refuses to start if they disagree, 09 §12).
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
DEFAULT_VERSION = "gbm_v2"


class FeatureMismatchError(RuntimeError):
    """metadata.json and the trained model disagree on features or classes."""


def model_dir(version: str | None = None) -> Path:
    return MODELS_DIR / (version or os.getenv("AGRI_MODEL_VERSION") or DEFAULT_VERSION)


class AgriModel:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else model_dir()
        self.metadata = json.loads((self.path / "metadata.json").read_text(encoding="utf-8"))
        self.features: list[str] = list(self.metadata["classifier_features"])
        self.classes: list[str] = list(self.metadata["class_labels"])
        self.version: str = self.metadata.get("model_version", self.path.name)
        self.cv: dict = self.metadata.get("cv", {})
        self._sk = None
        self._booster = None
        jl = self.path / "classifier.joblib"
        if jl.is_file():
            import joblib
            self._sk = joblib.load(jl)
            names = list(getattr(self._sk, "feature_name_", []) or [])
            model_classes = [int(c) for c in self._sk.classes_]
        else:
            import lightgbm as lgb
            self._booster = lgb.Booster(model_file=str(self.path / "classifier.txt"))
            names = list(self._booster.feature_name())
            model_classes = list(range(self._booster.num_model_per_iteration()))
        if names != self.features:
            raise FeatureMismatchError(f"{self.path}: model features {names} != metadata classifier_features "
                                       f"{self.features}")
        if any(c < 0 or c >= len(self.classes) for c in model_classes):
            raise FeatureMismatchError(f"model classes {model_classes} do not index class_labels {self.classes}")
        self._model_classes = model_classes

    # ---------------------------------------------------------------- input shaping
    def frame(self, data: Mapping[str, float] | pd.DataFrame) -> pd.DataFrame:
        """DataFrame with exactly the metadata feature columns, in metadata order (missing -> NaN, extra dropped)."""
        df = pd.DataFrame([dict(data)]) if not isinstance(data, pd.DataFrame) else data
        out = df.reindex(columns=self.features)
        return out.apply(pd.to_numeric, errors="coerce").astype(float)

    def missing(self, data: Mapping[str, float]) -> list[str]:
        return [f for f in self.features if data.get(f) is None or (isinstance(data.get(f), float) and math.isnan(data[f]))]

    # ---------------------------------------------------------------- prediction
    def predict_proba_frame(self, data: pd.DataFrame | Mapping[str, float]) -> np.ndarray:
        X = self.frame(data)
        raw = self._sk.predict_proba(X) if self._sk is not None else self._booster.predict(X.to_numpy())
        full = np.zeros((len(X), len(self.classes)))
        for col, cls in enumerate(self._model_classes):
            full[:, cls] = raw[:, col]
        return full

    def predict(self, features: Mapping[str, float]) -> dict[str, float]:
        """class_probs in metadata class order (healthy, watch, stressed, severe)."""
        p = self.predict_proba_frame(features)[0]
        return {c: round(float(v), 4) for c, v in zip(self.classes, p)}

    # ---------------------------------------------------------------- skill
    @property
    def beats_persistence(self) -> bool:
        cv = self.cv
        if "beats_persistence" in cv:
            return bool(cv["beats_persistence"])
        return (cv.get("model_accuracy", 0) > cv.get("persistence_accuracy", 1)
                and cv.get("model_macro_f1", 0) > cv.get("persistence_macro_f1", 1))

    @property
    def skill_factor(self) -> float:
        """Confidence multiplier: 0.8 when LOYO CV says the model does not beat persistence (honest discount)."""
        return 1.0 if self.beats_persistence else 0.8
