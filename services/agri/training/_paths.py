"""Paths for the training scripts, resolved relative to THIS file (never the CWD).

DATA_AGRI = <repo>/data/agri (or $DATA_DIR/agri via copilot_common.settings.get_data_dir()).
MODELS_DIR = services/agri/models
"""
from __future__ import annotations

import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]          # services/agri
REPO_ROOT = SERVICE_DIR.parents[1]
MODELS_DIR = SERVICE_DIR / "models"

if str(SERVICE_DIR) not in sys.path:                      # so `import agri` works when run as a script
    sys.path.insert(0, str(SERVICE_DIR))

try:
    from copilot_common.settings import get_data_dir
    DATA_AGRI = get_data_dir() / "agri"
except Exception:  # noqa: BLE001  copilot_common not installed: fall back to the repo layout
    DATA_AGRI = REPO_ROOT / "data" / "agri"
