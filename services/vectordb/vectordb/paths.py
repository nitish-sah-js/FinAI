"""Data file locations. Always under copilot_common's data dir (DATA_DIR override, else <repo>/data), never the CWD.

NAME CLASH: data/events.json belongs to the backtest (doc 12): it holds the 8 held-out events. The analog corpus
therefore lives in data/historical_events.json.
"""
from __future__ import annotations

from pathlib import Path

from copilot_common.settings import get_data_dir

CORPUS_FILE = "historical_events.json"      # 08 corpus (built by scripts/build_events.py)
SEED_FILE = "events_seed.csv"               # hand-written seed (08 §4)
CONFORMAL_FILE = "conformal_q.json"         # scripts/calibrate_conformal.py
HOLDOUT_FILE = "events.json"                # backtest holdout cases (12 §A1) — read only, never written here


def data_dir() -> Path:
    return get_data_dir()


def corpus_path() -> Path:
    return data_dir() / CORPUS_FILE


def seed_path() -> Path:
    return data_dir() / SEED_FILE


def conformal_path() -> Path:
    return data_dir() / CONFORMAL_FILE


def holdout_path() -> Path:
    return data_dir() / HOLDOUT_FILE
