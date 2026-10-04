"""deploy/cluster.py: config validation, per-laptop env rendering, UI env, bundle contents (no network)."""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cluster  # noqa: E402

GOOD = "L1_IP=192.168.1.11\nL2_IP=192.168.1.12\nL3_IP=192.168.1.13\nCLUSTER_KEY=\nMOCK=0\nDEMO_MODE=0\nVECTOR_PORT=9104\n"


def _cfg(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "cluster.env"
    p.write_text(text, encoding="utf-8")
    return p


@pytest.mark.parametrize("bad, msg", [
    ("L1_IP=\nL2_IP=192.168.1.2\nL3_IP=192.168.1.3\n", "L1_IP is missing"),
    ("L1_IP=192.168.1.x\nL2_IP=192.168.1.2\nL3_IP=192.168.1.3\n", "placeholder"),
    ("L1_IP=192.168.1.1\nL2_IP=banana\nL3_IP=192.168.1.3\n", "not an IP"),
    ("L1_IP=192.168.1.1\nL2_IP=127.0.0.1\nL3_IP=192.168.1.3\n", "cannot be reached"),
    ("L1_IP=192.168.1.1\nL2_IP=192.168.1.1\nL3_IP=192.168.1.3\n", "must differ"),
    ("L1_IP=192.168.1.1\nL2_IP=192.168.1.2\nL3_IP=192.168.1.3\nORCH_PORT=99999\n", "not a port"),
])
def test_rejects_bad_config(tmp_path, bad, msg):
    with pytest.raises(cluster.ConfigError, match=msg):
        cluster.load_cluster(_cfg(tmp_path, bad))


def test_missing_file_is_an_error(tmp_path):
    with pytest.raises(cluster.ConfigError, match="not found"):
        cluster.load_cluster(tmp_path / "nope.env")


def test_key_generated_and_saved(tmp_path):
    p = _cfg(tmp_path, GOOD)
    c = cluster.load_cluster(p)
    key = cluster.ensure_key(c, p)
    assert len(key) == 32 and f"CLUSTER_KEY={key}" in p.read_text()
    with pytest.raises(cluster.ConfigError, match="16"):
        cluster.ensure_key({"CLUSTER_KEY": "short"}, p)


def test_render_laptop_env(tmp_path):
    p = _cfg(tmp_path, GOOD)
    c = cluster.load_cluster(p)
    cluster.ensure_key(c, p)
    env = cluster.render_laptop(c, "L2", {"SMTP_USER": "me@example.com", "L1_HOST": "1.1.1.1", "CLUSTER_KEY": "x"})
    assert "THIS_LAPTOP=L2" in env
    assert "L1_HOST=192.168.1.11" in env and "L3_HOST=192.168.1.13" in env
    assert "VECTOR_URL=http://${L2_HOST}:9104" in env and "VECTOR_PORT=9104" in env
    assert "ORCH_URL=http://${L1_HOST}:8000" in env
    assert f"CLUSTER_KEY={c['CLUSTER_KEY']}" in env
    assert "SMTP_USER=me@example.com" in env      # secret carried from the root .env
    assert "MOCK=0" in env


def test_ui_env_points_at_each_laptop(tmp_path):
    p = _cfg(tmp_path, GOOD)
    c = cluster.load_cluster(p)
    cluster.ensure_key(c, p)
    ui = cluster.ui_env(c)
    assert "NEXT_PUBLIC_ORCH_URL=http://192.168.1.11:8000" in ui
    assert "NEXT_PUBLIC_VECTOR_URL=http://192.168.1.12:9104" in ui
    assert "NEXT_PUBLIC_MONITOR_URL=http://192.168.1.13:8202" in ui
    assert f"NEXT_PUBLIC_CLUSTER_KEY={c['CLUSTER_KEY']}" in ui


def test_gen_and_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(cluster, "OUT", tmp_path / "out")
    p = _cfg(tmp_path, GOOD)
    assert cluster.main(["gen", "--config", str(p)]) == 0
    assert cluster.main(["bundle", "--config", str(p)]) == 0
    for lap, must, never in [
        ("L2", ["services/quant/quant/main.py", "services/agri/models", "data/historical_events.json",
                "infra/docker-compose.yml", "setup_L2.ps1", "start_L2.ps1", "stop_L2.ps1", "health_L2.ps1", ".env",
                "README.md", "packages/copilot_common/copilot_common/auth.py"],
         ["services/orchestrator", "apps/terminal", "data/ledger.db", "data/logs", "data/cache"]),
        ("L3", ["services/ingestion/app.py", "services/monitor/monitor/main.py", "apps/terminal/package.json",
                "apps/terminal/.env.local", "start_L3.ps1", ".env"],
         ["services/quant", "apps/terminal/node_modules", "apps/terminal/.next", "data/alerts.db"]),
    ]:
        names = zipfile.ZipFile(tmp_path / "out" / f"{lap}_bundle.zip").namelist()
        for m in must:
            assert any(n == m or n.startswith(m + "/") for n in names), f"{lap} bundle lacks {m}"
        for n_ in never:
            assert not any(n == n_ or n.startswith(n_ + "/") for n in names), f"{lap} bundle has {n_}"
        env = (tmp_path / "out" / f"{lap}_bundle" / ".env").read_text()
        assert f"THIS_LAPTOP={lap}" in env
    start = (tmp_path / "out" / "L2_bundle" / "start_L2.ps1").read_text()
    assert "-Laptop L2" in start and "docker compose" in start


def test_bundle_needs_gen_first(tmp_path, monkeypatch):
    monkeypatch.setattr(cluster, "OUT", tmp_path / "out")
    assert cluster.main(["bundle", "--config", str(_cfg(tmp_path, GOOD))]) == 2


def test_setup_script_stops_on_failure_and_needs_no_python_312_packages(tmp_path, monkeypatch):
    """L3 (Python 3.11) once got 'setup done' although pip had failed: earthaccess needs 3.12 and was in
    requirements.txt. Every native step is now checked, and requirements.txt stays installable on 3.11."""
    monkeypatch.setattr(cluster, "OUT", tmp_path / "out")
    p = _cfg(tmp_path, GOOD)
    assert cluster.main(["gen", "--config", str(p)]) == 0 and cluster.main(["bundle", "--config", str(p)]) == 0
    for lap in ("L2", "L3"):
        s = (tmp_path / "out" / f"{lap}_bundle" / f"setup_{lap}.ps1").read_text()
        assert 'Check "pip install (Python packages)"' in s and "sys.version_info >= (3, 11)" in s
        pulls = [ln for ln in s.splitlines() if ln.strip().startswith("ollama pull")]
        assert pulls and all('Check "ollama pull' in ln for ln in pulls)
    assert 'Check "npm install"' in (tmp_path / "out" / "L3_bundle" / "setup_L3.ps1").read_text()
    assert 'Check "docker compose' in (tmp_path / "out" / "L2_bundle" / "setup_L2.ps1").read_text()
    reqs = (cluster.ROOT / "requirements.txt").read_text()
    assert "earthaccess" not in reqs and "s3fs" not in reqs
