"""predict.py: feature/class order only from metadata, column-reordering guard, refuse mismatched artifacts."""
import json
import shutil

import numpy as np
import pandas as pd
import pytest

from agri.predict import AgriModel, FeatureMismatchError, model_dir


@pytest.fixture(scope="module")
def frame(model, training_csv):
    t = training_csv.dropna(subset=["stress_class_now"]).copy()
    t["stress_class_now_id"] = t["stress_class_now"].map({c: i for i, c in enumerate(model.classes)})
    return t[model.features].dropna().head(300).reset_index(drop=True)


def test_metadata_order(model):
    md = json.loads((model.path / "metadata.json").read_text(encoding="utf-8"))
    assert model.features == md["classifier_features"]
    assert model.classes == md["class_labels"] == ["healthy", "watch", "stressed", "severe"]


@pytest.mark.filterwarnings("ignore:X does not have valid feature names")
def test_column_reordering_guard(model, frame):
    ordered = model.predict_proba_frame(frame)
    shuffled_cols = list(reversed(frame.columns))
    shuffled = frame[shuffled_cols]
    # our guard re-indexes -> identical
    assert np.allclose(model.predict_proba_frame(shuffled), ordered)
    # ...whereas the raw estimator silently accepts the wrong order and gives different answers (the hazard)
    raw = model._sk.predict_proba(shuffled.to_numpy())
    assert not np.allclose(raw, ordered)


def test_dict_input_any_key_order(model, frame):
    row = frame.iloc[0].to_dict()
    rev = dict(reversed(list(row.items())))
    assert model.predict(row) == model.predict(rev)
    probs = model.predict(row)
    assert list(probs) == model.classes and abs(sum(probs.values()) - 1) < 1e-3


def test_missing_and_extra_features(model, frame):
    row = frame.iloc[0].to_dict()
    row.pop("ndvi_delta")
    row["unused_extra"] = 123.0
    assert model.missing(row) == ["ndvi_delta"]
    assert list(model.frame(row).columns) == model.features


def test_refuses_mismatched_metadata(tmp_path):
    src = model_dir()
    dst = tmp_path / "bad"
    shutil.copytree(src, dst)
    md = json.loads((dst / "metadata.json").read_text(encoding="utf-8"))
    md["classifier_features"] = list(reversed(md["classifier_features"]))
    (dst / "metadata.json").write_text(json.dumps(md), encoding="utf-8")
    with pytest.raises(FeatureMismatchError):
        AgriModel(dst)


def test_booster_fallback_matches_joblib(tmp_path, model, frame):
    dst = tmp_path / "txt_only"
    shutil.copytree(model_dir(), dst)
    (dst / "classifier.joblib").unlink()
    m2 = AgriModel(dst)
    assert m2._booster is not None
    assert np.allclose(m2.predict_proba_frame(frame), model.predict_proba_frame(frame), atol=1e-9)


def test_skill_factor_reflects_cv(model):
    cv = model.cv
    beats = cv["model_accuracy"] > cv["persistence_accuracy"] and cv["model_macro_f1"] > cv["persistence_macro_f1"]
    assert model.beats_persistence == beats
    assert model.skill_factor == (1.0 if beats else 0.8)
