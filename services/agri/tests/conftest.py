"""Shared fixtures. Unit tests run with MOCK off (the MOCK test switches it on explicitly)."""
import os

os.environ["MOCK"] = "0"
os.environ["MOCK_DELAY_MS"] = "0"

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from copilot_common.settings import get_data_dir, reload_settings  # noqa: E402

reload_settings()


@pytest.fixture(scope="session")
def engine():
    from agri.main import get_engine
    return get_engine()


@pytest.fixture(scope="session")
def model(engine):
    return engine[0]


@pytest.fixture(scope="session")
def store(engine):
    return engine[1]


@pytest.fixture(scope="session")
def training_csv():
    p = get_data_dir() / "agri" / "agri_training.csv"
    if not p.is_file():
        pytest.skip("data/agri/agri_training.csv missing (run services/agri/training/build_table.py)")
    return pd.read_csv(p, parse_dates=["period_end", "image_date"])


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from agri.main import app
    return TestClient(app)
